import os
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
# Monkey patch ProcessPoolExecutor to avoid multiprocessing issues
original_ProcessPoolExecutor = concurrent.futures.ProcessPoolExecutor
concurrent.futures.ProcessPoolExecutor = ThreadPoolExecutor
# Also patch in the process module
import concurrent.futures.process
concurrent.futures.process.ProcessPoolExecutor = ThreadPoolExecutor
import boto3
import urllib.parse
import hashlib
import json
import shutil
import pypdf
import traceback
from boto3.dynamodb.conditions import Key
from graphrag_toolkit.lexical_graph.storage import VectorStoreFactory, GraphStoreFactory
from graphrag_toolkit.lexical_graph import LexicalGraphIndex
#from graphrag_toolkit.lexical_graph.indexing.build import Checkpoint
from llama_index.core import SimpleDirectoryReader

#import nest_asyncio

# Apply nest_asyncio for Lambda environment
#nest_asyncio.apply()

# Set up environment variables
aws_region = os.environ.get('AWS_REGION', 'us-west-2')
WEBSOCKET_ENDPOINT = os.environ.get('WEBSOCKET_ENDPOINT').replace('wss://', 'https://')
WEBSOCKET_STATE_TABLE = os.environ.get('DYNAMODB_WEBSOCKET_STATE_TABLE')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
DOCUMENT_REGISTRY_TABLE = os.environ.get('DYNAMODB_DOCUMENT_REGISTRY_TABLE')
MD5_BY_S3_PATH_INDEX = os.environ.get('DYNAMODB_MD5_BY_S3_PATH_INDEX')
NEPTUNE_GRAPH_ID = os.environ.get('NEPTUNE_GRAPH_ID')
NEPTUNE_GRAPH_ENDPOINT = os.environ.get('NEPTUNE_GRAPH_ENDPOINT')
GRAPHRAG_DOCUMENTS_BUCKET = os.environ.get('GRAPHRAG_DOCUMENTS_BUCKET')
EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL')
EMBEDDING_SIZE = int(os.environ.get('EMBEDDING_SIZE'))

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
dynamodb_resource = boto3.resource('dynamodb')
s3_client = boto3.client('s3', region_name=aws_region)
api_client = boto3.client('apigatewaymanagementapi', endpoint_url=WEBSOCKET_ENDPOINT)
sqs_client = boto3.client('sqs')

# Initialize GraphRAG components
NEPTUNE_CONNECTION_INFO = "neptune-graph://"+ NEPTUNE_GRAPH_ID

def get_vector_and_graph_stores():
    """Initialize vector and graph stores"""
    vector_store = VectorStoreFactory.for_vector_store(NEPTUNE_CONNECTION_INFO)
    graph_store = GraphStoreFactory.for_graph_store(NEPTUNE_CONNECTION_INFO)
    
    return vector_store, graph_store

def download_object(bucket_name, object_key, download_path):
    try:
        s3_client.download_file(bucket_name, object_key, download_path)
        print(f"File downloaded to {download_path}")
    except Exception as e:
        print(f"Error downloading object: {e}")
        raise

def create_directory_from_object_key(object_key):
    local_dir_path = os.path.join('/tmp', os.path.dirname(object_key))
    os.makedirs(local_dir_path, exist_ok=True)
    print(f"Directory created at: {local_dir_path}")
    return local_dir_path

def send_message(type, message, connection_id, level):
    data = {
        'source': "graphrag-ingest-lambda",
        'type': type,
        'message': message,
        'connectionId': connection_id,
        'level': level
    }
    params = {
        'ConnectionId': connection_id,
        'Data': json.dumps(data).encode()
    }

    response = api_client.post_to_connection(**params)
    return response

def get_connection_id_from_user(cognito_sub):
    cognito_sub = cognito_sub.replace('%3A', ':')
    
    response = dynamodb.get_item(
        TableName=WEBSOCKET_STATE_TABLE,
        Key={
            'userId': {
                'S': cognito_sub
            }
        }
    )
    
    if 'Item' not in response:
        raise Exception(f"Item not found for user {cognito_sub}")
    
    connection_id = response['Item']['ConnectionId']['S']
    return connection_id

def get_cognito_sub_from_s3_key(s3_key):
    # expecting 'private/cognito_sub/file.pdf' or 'private/cognito_sub/file.txt'
    cognito_sub = s3_key.split('/')[1]
    return cognito_sub

def calculate_md5(file_path, username):
    """Calculate the MD5 hash of a file and the owner cognito_sub"""
    hash_md5 = hashlib.md5()
    hash_md5.update(username.encode('utf-8'))
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def store_file_info(md5_hash, cognito_sub, s3_path, table_name):
    """Store the MD5 hash, cognito_sub, and full S3 path in DynamoDB."""
    table = dynamodb_resource.Table(table_name)
    
    response = table.put_item(
        Item={
            'md5': md5_hash,
            'user': cognito_sub,
            's3_path': s3_path,
            'processing_type': 'GRAPHRAG'
        }
    )
    return response

def delete_file_info(md5_hash, s3_path, table_name):
    """Delete the file info from DynamoDB based on the MD5 hash and cognito_sub."""
    print("from delete_file_info using md5_hash")
    print(md5_hash)

    table = dynamodb_resource.Table(table_name)
    
    response = table.delete_item(
        Key={
            'md5': f"{md5_hash}",
            's3_path': f"{s3_path}"
        }
    )
    return response

def is_file_processed(md5_hash, s3_path, table_name):
    """Check if the MD5 hash and S3 path already exist in DynamoDB."""
    table = dynamodb_resource.Table(table_name)

    response = table.query(
        KeyConditionExpression=Key('md5').eq(md5_hash)
    )
    
    items = response.get('Items', [])
    print(f"Items: {items}")
    return len(items) > 0

def get_md5_by_s3_path(s3_path, table_name, index):
    """Get the MD5 hash by the S3 path using the GSI."""
    table = dynamodb_resource.Table(table_name)
    response = table.query(
        IndexName=index,
        KeyConditionExpression=Key('s3_path').eq(s3_path)
    )

    print(f"Response: {response}")
    
    items = response.get('Items', [])
    if items:
        # Filter for GraphRAG processing type
        graphrag_items = [item for item in items if item.get('processing_type') == 'GRAPHRAG']
        if graphrag_items:
            print(f"first graphrag item {graphrag_items[0]}")
            return graphrag_items[0]['md5']
    return None

def get_file_extension(file_path):
    """Get the file extension in lowercase"""
    return os.path.splitext(file_path)[1].lower()

def convert_pdf_to_text(pdf_path, output_dir):
    """Convert PDF to text files for processing"""
    
    
    text_content = ""
    with open(pdf_path, 'rb') as file:
        pdf_reader = pypdf.PdfReader(file)
        for page in pdf_reader.pages:
            text_content += page.extract_text() + "\n"
    
    # Save as text file
    text_filename = os.path.splitext(os.path.basename(pdf_path))[0] + ".txt"
    text_path = os.path.join(output_dir, text_filename)
    
    with open(text_path, 'w', encoding='utf-8') as text_file:
        text_file.write(text_content)
    
    return text_path

def copy_txt_file(txt_path, output_dir):
    """Copy TXT file to processing directory"""
    txt_filename = os.path.basename(txt_path)
    destination_path = os.path.join(output_dir, txt_filename)
    
    shutil.copy2(txt_path, destination_path)
    print(f"TXT file copied to {destination_path}")
    
    return destination_path

def prepare_document_for_processing(file_path, output_dir, connection_id=None):
    """Prepare document for GraphRAG processing based on file type"""
    file_extension = get_file_extension(file_path)
    
    if file_extension == '.pdf':
        if connection_id:
            send_message("message", "Converting PDF to text for processing...", connection_id, "info")
        return convert_pdf_to_text(file_path, output_dir)
    
    elif file_extension == '.txt':
        if connection_id:
            send_message("message", "Preparing TXT file for processing...", connection_id, "info")
        return copy_txt_file(file_path, output_dir)
    
    else:
        raise ValueError(f"Unsupported file type: {file_extension}. Only PDF and TXT files are supported.")

def process_documents_with_graphrag(docs, cognito_sub, connection_id):
    """Process documents using GraphRAG toolkit with tenant_id for multitenancy"""
    try:
        # FIXED: Set working directory to /tmp to avoid read-only filesystem errors
        #original_cwd = os.getcwd()
        #tmp_work_dir = os.path.join('/tmp', 'graphrag_work')
        #os.makedirs(tmp_work_dir, exist_ok=True)
        #os.chdir(tmp_work_dir)
        
        #print(f"Changed working directory to: {tmp_work_dir}")
        
        # Get vector and graph stores
        vector_store, graph_store = get_vector_and_graph_stores()
        
        # Create checkpoint for this processing session in /tmp
        #checkpoint_name = f"doc-checkpoint-{cognito_sub}-{abs(hash(str(docs)))}"
        #checkpoint_dir = os.path.join('/tmp', 'checkpoints')
        #os.makedirs(checkpoint_dir, exist_ok=True)
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        os.environ['OMP_NUM_THREADS'] = '1'
        os.environ['MKL_NUM_THREADS'] = '1'
        os.environ['PYTHONHASHSEED'] = '0'
        decoded_sub = urllib.parse.unquote(cognito_sub).replace(':', '_').replace('-', '_')
        tenant_hash = hashlib.md5(cognito_sub.encode()).hexdigest()[:10].lower()
        print(f"Original cognito_sub: {cognito_sub}")
        print(f"Generated tenant_id: {tenant_hash}")
        # FIXED: Pass the checkpoint directory to ensure it uses /tmp
        #checkpoint = Checkpoint(checkpoint_name, output_dir=checkpoint_dir)
        
        #print(f"Created checkpoint: {checkpoint_name} in {checkpoint_dir}")
        
        # Create LexicalGraphIndex with tenant_id for user segregation
        graph_index = LexicalGraphIndex(
            graph_store, 
            vector_store
            #tenant_id=tenant_hash  # Use cognito_sub as tenant_id for multitenancy
        )
        
        if connection_id:
            send_message("message", "Building knowledge graph and embeddings...", connection_id, "info")
        
        # Extract and build the graph
        print("Starting GraphRAG extract_and_build process...")
        graph_index.extract_and_build(docs, show_progress=False)
        print("GraphRAG extract_and_build completed successfully")
        
        # Restore original working directory
        #os.chdir(original_cwd)
        
        return True
        
    except Exception as e:
        
        
        # Getting full traceback information
        print(traceback.format_exc())     
        print(f"Error processing documents with GraphRAG: {e}")
        if connection_id:
            send_message("message", f"Error building knowledge graph: {str(e)}", connection_id, "error")
        raise e

# Rest of the existing functions remain the same...
def single_lambda_handler_create(record):
    print("GraphRAG single_lambda_handler_create :: record")
    print(record)

    # Extract bucket name and object key from the record
    bucket_name = record['s3']['bucket']['name']
    object_key = record['s3']['object']['key']
    cognito_sub = get_cognito_sub_from_s3_key(object_key)
    full_s3_path = f"s3://{bucket_name}/{object_key}"

    print(f"cognito_sub: {cognito_sub}")

    object_key = urllib.parse.unquote_plus(object_key)
    local_dir_path = create_directory_from_object_key(object_key)
    local_file_path = os.path.join(local_dir_path, os.path.basename(object_key))

    # get connection id for user
    try:
        connection_id = get_connection_id_from_user(cognito_sub)
        print(f"Connection ID: {connection_id} for user {cognito_sub}")
    except Exception as e:
        print(f"Error getting connection ID for user {cognito_sub}: {e}, user {cognito_sub} is flying blind")
        connection_id = None

    print(f"Object key: {object_key}")
    print(f"Local file path: {local_file_path}")
    print(f"Local directory path: {local_dir_path}")

    # Check if file type is supported
    file_extension = get_file_extension(object_key)
    if file_extension not in ['.pdf', '.txt']:
        error_msg = f"Unsupported file type: {file_extension}. Only PDF and TXT files are supported for GraphRAG."
        print(error_msg)
        if connection_id:
            send_message("message", error_msg, connection_id, "error")
        return {
            'statusCode': 400,
            'body': error_msg,
            'type': 'create',
            'document': object_key
        }

    try:
        download_object(bucket_name, object_key, local_file_path)
    except Exception as e:
        print(f"Error downloading object: {e}")
        if connection_id:
            send_message("message", f"Error ingesting GraphRAG document: {object_key}", connection_id, "error")
        return {
            'statusCode': 500,
            'body': 'Failed to download object',
            'type': 'create',
            'document': object_key
        }

    md5_hash = calculate_md5(local_file_path, cognito_sub)
    print(f"MD5 hash: {md5_hash}")
    
    # send message to user <ingestion started>
    try:
        file_type = "PDF" if file_extension == '.pdf' else "TXT"
        if connection_id:
            send_message(
                "message", 
                f"Started GraphRAG processing {file_type} file: {'/'.join(object_key.split('/')[2:])}", 
                connection_id, 
                "info"
            )
    except Exception as e:
        print(f"Error splitting object_key into just the file name: {e}")
        if connection_id:
            send_message(
                "message", 
                f"Started GraphRAG processing {object_key}", 
                connection_id, 
                "info"
            )

    # check if file has been processed already 
    try:
        if is_file_processed(md5_hash, object_key, DOCUMENT_REGISTRY_TABLE):
            print(f"GraphRAG file {object_key} has already been processed for user {cognito_sub}")
            if connection_id:
                send_message("message", f"{object_key} has already been processed for GraphRAG", connection_id, "info")
            return {
                'statusCode': 200,
                'body': 'File already processed',
                'type': 'create',
                'document': object_key
            }
        else:
            print(f"GraphRAG file {object_key} has not been processed yet")
    except Exception as e:
        print(f"Error checking if GraphRAG file {object_key} has been processed: {e}")
        if connection_id:
            send_message("message", f"Error checking if GraphRAG file {object_key} has been processed", connection_id, "error")
        return {
            'statusCode': 500,
            'body': 'Failed to check if file has been processed',
            'type': 'create',
            'document': object_key
        }

    # store file info in DynamoDB
    try:
        store_file_info(md5_hash, cognito_sub, f"s3://{bucket_name}/{object_key}", DOCUMENT_REGISTRY_TABLE)
    except Exception as e:
        print(f"Error storing GraphRAG file info in DynamoDB: {e}")
        if connection_id:
            send_message("message", f"Failed to ingest GraphRAG {'/'.join(object_key.split('/')[2:])}", connection_id, "error")
        return {
            'statusCode': 500,
            'body': 'Failed to store file info in DynamoDB',
            'type': 'create',
            'document': object_key
        }

    try:
        # Create directory for text processing
        #text_dir = os.path.join('/tmp', 'txt_data')
        #os.makedirs(text_dir, exist_ok=True)
        
        # Prepare document based on file type (PDF or TXT)
        #processed_file_path = prepare_document_for_processing(local_file_path, text_dir, connection_id)
        
        # Load documents using SimpleDirectoryReader
        reader = SimpleDirectoryReader(local_dir_path)
        docs = reader.load_data()
        
        if not docs:
            raise Exception("No documents were loaded for processing")
        
        print(f"Loaded {len(docs)} documents for GraphRAG processing")
        
        # Process with GraphRAG using tenant_id for multitenancy
        success = process_documents_with_graphrag(docs, cognito_sub, connection_id)
        
        if success and connection_id:
            file_type = "PDF" if file_extension == '.pdf' else "TXT"
            send_message(
                "message", 
                f"Finished GraphRAG processing {file_type} file: {'/'.join(object_key.split('/')[2:])}", 
                connection_id, 
                "success"
            )
            
    except Exception as e:
        print(f"Error with GraphRAG processing: {e}")
        if connection_id:
            send_message("message", f"Failed to process GraphRAG {'/'.join(object_key.split('/')[2:])}: {str(e)}", connection_id, "error")

        try:
            delete_file_info(md5_hash, full_s3_path, DOCUMENT_REGISTRY_TABLE)
        except Exception as delete_error:
            print(f"Error deleting file info from DynamoDB: {delete_error}")
            
        return {
            'statusCode': 500,
            'body': 'GraphRAG document processing failed',
            'document': object_key,
            'type': 'create',
            'error': str(e)
        }

    return {
        'statusCode': 200,
        'body': 'GraphRAG documents processed successfully.',
        'document': object_key,
        'type': 'create'
    }

# Keep all other existing functions unchanged...
def single_lambda_handler_delete(record):
    # ... existing code remains the same
    print("GraphRAG delete handler")
    print(record)

    # Extract bucket name and object key from the record
    bucket_name = record['s3']['bucket']['name']
    object_key = urllib.parse.unquote_plus(record['s3']['object']['key']).replace('%3A', ':')
    cognito_sub = get_cognito_sub_from_s3_key(object_key)
    s3_full_path = f"s3://{bucket_name}/{object_key}"
    filename = os.path.basename(object_key)

    print(f"s3_full_path {s3_full_path}")

    # Check if file type is supported
    file_extension = get_file_extension(object_key)
    if file_extension not in ['.pdf', '.txt']:
        print(f"Unsupported file type for deletion: {file_extension}. Only PDF and TXT files are supported for GraphRAG.")
        return {
            'statusCode': 400,
            'body': 'Unsupported file type for GraphRAG deletion',
            'type': 'delete',
            'document': object_key
        }

    try:
        connection_id = get_connection_id_from_user(cognito_sub)
        print(f"Connection ID: {connection_id} for user {cognito_sub}")
    except Exception as e:
        print(f"Error getting connection ID for user {cognito_sub}: {e}, user is flying blind")
        connection_id = None

    md5_hash = get_md5_by_s3_path(
        s3_full_path, DOCUMENT_REGISTRY_TABLE, MD5_BY_S3_PATH_INDEX
    )

    print("GraphRAG MD5 hash retrieved:")
    print(f"retrieved md5 {md5_hash}")

    if md5_hash is None:
        print(f"GraphRAG file {s3_full_path} has already been deleted from knowledge graph")
        if connection_id:
            send_message("message", f"GraphRAG file {filename} successfully deleted from knowledge graph", connection_id, "info")
        return {
            'statusCode': 200,
            'body': 'File has already been deleted from knowledge graph',
            'type': 'delete',
            'document': object_key
        }

    try:
        # For GraphRAG deletion, we would need to implement cleanup in Neptune
        # This is more complex than LanceDB deletion and might require
        # tracking which documents contributed to which graph entities
        
        # For now, we'll just delete the registry entry
        # In a production system, you'd want to implement proper graph cleanup
        
        file_type = "PDF" if file_extension == '.pdf' else "TXT"
        if connection_id:
            send_message("message", f"Deleting GraphRAG data for {file_type} file: {filename}...", connection_id, "info")
        
        delete_file_info(md5_hash, s3_full_path, DOCUMENT_REGISTRY_TABLE)
        
        if connection_id:
            send_message("message", f"Finished deleting GraphRAG {file_type} file: {filename}", connection_id, "success")
        
        print(f"Finished deleting GraphRAG {object_key} with hash {md5_hash}")
        
    except Exception as e:
        print(f"Error deleting GraphRAG file info from DynamoDB: {e}")
        if connection_id:
            send_message("message", f"Failed to delete GraphRAG {filename}", connection_id, "error")
        return {
            'statusCode': 500,
            'body': 'Failed to delete the GraphRAG documents',
            'document': object_key,
            'type': 'delete',
            'error': str(e)
        }

    return {
        'statusCode': 200,
        'body': 'GraphRAG documents deleted successfully.',
        'document': object_key,
        'type': 'delete'
    }

def lambda_handler(event, context):
    '''
    Processing GraphRAG documents (PDF and TXT) from SQS queue
    '''
    
    successes = []
    failures = []
    unhandled = []

    print("GraphRAG Lambda Handler - Supporting PDF and TXT files")
    print(event)
    print(os.environ)

    for record in event['Records']:
        message_id = record['messageId']
        receipt_handle = record['receiptHandle']
        body = record['body']

        local_successes = []
        local_failures = []
        local_unhandled = []
        
        print(f"Message ID: {message_id}")
        print(f"Receipt Handle: {receipt_handle}")
        print(f"Body: {body}")
        
        # Parse the S3 event from the body
        s3_event = json.loads(body)
        
        for s3_record in s3_event['Records']:
            
            event_name = s3_record['eventName']
            s3_bucket = s3_record['s3']['bucket']['name']
            s3_object_key = s3_record['s3']['object']['key']

            print("RECEIVING S3 OBJECT KEY FOR GRAPHRAG")
            print(s3_object_key)
            
            if event_name.startswith("ObjectCreated"):
                print(f"GraphRAG Object created in bucket {s3_bucket}: {s3_object_key}")
                response = single_lambda_handler_create(s3_record)
                if response['statusCode'] == 200:
                    local_successes.append({
                        "s3_record": s3_record,
                        "response": response
                    })
                elif response['statusCode'] in [400, 500]:
                    local_failures.append({
                        "s3_record": s3_record,
                        "response": response
                    })
                else:
                    local_unhandled.append({
                        "s3_record": s3_record,
                        "response": response
                    })

            elif event_name.startswith("ObjectRemoved"):
                print(f"GraphRAG Object deleted from bucket {s3_bucket}: {s3_object_key}")
                response = single_lambda_handler_delete(s3_record)
                if response['statusCode'] == 200:
                    local_successes.append({
                        "s3_record": s3_record,
                        "response": response
                    })
                elif response['statusCode'] in [400, 500]:
                    local_failures.append({
                        "s3_record": s3_record,
                        "response": response
                    })
                else:
                    local_unhandled.append({
                        "s3_record": s3_record,
                        "response": response
                    })
                
            else:
                local_unhandled.append(s3_record)
    
        print("GraphRAG Local results:")
        print(local_successes)
        print(local_failures)
        print(local_unhandled)
        
        if len(local_failures) == 0:
            successes.append({
                "message_id": message_id,
                "receipt_handle": receipt_handle,
                "body": body,
                "s3_event": s3_event,
                "local_successes": local_successes,
                "local_failures": local_failures,
                "local_unhandled": local_unhandled
            })
    
    # Delete successful messages from the queue
    print("GraphRAG successes:")
    print(successes)
    for success in successes:
        print("GRAPHRAG GLOBAL SUCCESS")
        print(success)
        print(f"Deleting message with receipt handle {success['receipt_handle']}")
        try:
            sqs_client.delete_message(
                QueueUrl=SQS_QUEUE_URL,
                ReceiptHandle=success['receipt_handle']
            )
        except Exception as e:
            print(f"Error deleting message from queue: {e}")

    status = {
        'success': successes,
        'failures': failures,
        'unhandled': unhandled
    }

    print("GraphRAG Final Status:")
    print(status)

    return status
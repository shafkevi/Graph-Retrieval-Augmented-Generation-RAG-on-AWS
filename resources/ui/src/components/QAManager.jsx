import React, { useState, useEffect} from 'react';
import PropTypes from 'prop-types';
import { SignatureV4 } from "@smithy/signature-v4";
import { Sha256 } from "@aws-crypto/sha256-js";
import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import Markdown from 'react-markdown';
import rehypeRaw from 'rehype-raw'
import {Prism as SyntaxHighlighter} from 'react-syntax-highlighter'
import darkMarkdown from '../static/themes/awsDark.js';
import { useNavigate } from "react-router-dom";

import {
  Container,
  Select,
  SpaceBetween,
  Textarea,
  Button,
  Header,
  FormField,
  Link, 
  Box,
  Alert,
  Toggle,
  Badge,
} from '@cloudscape-design/components'

import { BedrockClient, ListFoundationModelsCommand } from '@aws-sdk/client-bedrock';

import { streamingLambda, syncLambda } from './helpers';

export function QAManager({ inferenceURL, creds, region, appConfig }) {

  const navigate = useNavigate();

  const [searchQuery, setSearchQuery] = useState(() => {
    const savedQuery = localStorage.getItem('searchQuery');
    return savedQuery || '';
  });

  // Add GraphRAG toggle state
  const [isGraphRAG, setIsGraphRAG] = useState(() => {
    const savedMode = localStorage.getItem('ragMode');
    return savedMode === 'graphrag';
  });

  const [models, setModels] = useState([]);
  const localStorageModel = localStorage.getItem('llm_model_id') || 'loading...';

  let chatHistory = [];
  try {
    const storedHistory = localStorage.getItem('chat_history');
    if (storedHistory) {
      chatHistory = JSON.parse(storedHistory);
    }
  } catch (error) {
    console.log('Error parsing chat history', error);
    // Ensure chatHistory is always an array even if parsing fails
  }
  
  const [model, setModel] = useState(localStorageModel);

  const [searching, setSearching] = useState();
  const [metadata, setMetadata] = useState([]);
  const [results, setResults] = useState([]);
  const [systemPrompt, setSystemPrompt] = useState(() => {
    const savedPrompt = localStorage.getItem('parameterEditorState');
    if(savedPrompt){
      const parsedPrompt = JSON.parse(savedPrompt);
      if (parsedPrompt.some(item => item.isChecked)) {
        return {
          isModified: true,
          values: parsedPrompt
        }
      }
    }
    return {
      isModified: false,
      values:[]
    }
  });

  // Save RAG mode to localStorage when it changes
  useEffect(() => {
    localStorage.setItem('ragMode', isGraphRAG ? 'graphrag' : 'regular');
  }, [isGraphRAG]);

  // when searchQuery changes, store it into the local storage
  useEffect(() => {
      localStorage.setItem('searchQuery', searchQuery);
  });

  const clearResponse = () => {
    setSearching(false);
    setMetadata([]);
    setResults([]);
  }

  const getPresignedUrlAndRedirect = async (objectKey, page) => {
    // Determine which bucket to use based on RAG mode and metadata source
    const bucketName = isGraphRAG ? appConfig.graphRagStorage.bucket_name : appConfig.storage.bucket_name;
    
    const s3Client = new S3Client({
      region: appConfig.storage.aws_region,
      credentials: {
        accessKeyId: creds.accessKeyId,
        secretAccessKey: creds.secretAccessKey,
        sessionToken: creds.sessionToken,
      }
    });
  
    const command = new GetObjectCommand({
      Bucket: bucketName,
      Key: objectKey,
    });
  
    try {
      const signedUrl = await getSignedUrl(s3Client, command, { expiresIn: 3600 });
      if (page){
        window.open(`${signedUrl}#page=${page}`, '_blank');
      }
      else {
        window.open(signedUrl, '_blank');
      }
    } catch (error) {
      console.error('Error generating pre-signed URL', error);
    }
  };

  const getPromptOverrideObject = (systemPromptState) =>{
    console.log("getPromptOverrideObject");
    console.log(systemPromptState);
    const override = {};
    for (const prompt of systemPromptState.values){
      if(prompt.isChecked){
        override[prompt.name.split("/").pop()] = prompt.userInput;
      }
    }
    return override;
  }

  const prependQuestionToHistory = question => {
    const newQAPair = {
      question,
      answer: '',
      date: new Date().toISOString(),
      checked: false,
      model,
      ragType: isGraphRAG ? 'graphrag' : 'regular' // Add RAG type to history
    }
    const updatedChatHistory = [newQAPair, ...chatHistory];
    localStorage.setItem('chat_history', JSON.stringify(updatedChatHistory));
  }

  const setResponseToLastQuestionInChatHistory = answer => {
    const lastMessage = chatHistory.shift();
    if(!lastMessage) return;
    lastMessage.answer = answer;
    lastMessage.checked = true;
    chatHistory.unshift(lastMessage);
    localStorage.setItem('chat_history', JSON.stringify(lastMessage));
  }

  const getHistoryForConverseAPI = () => {
    const transformedHistory = [];
    for (const message of chatHistory.filter( item => item.checked)) {
      transformedHistory.push({
        role: "user",
        content: [{ text: message.question }]
      });
      transformedHistory.push({
        role: "assistant",
        content: [{ text: message.answer }]
      });
    }
    return transformedHistory;
  }

  const getData = async (streaming = true) => {
    clearResponse();
    prependQuestionToHistory(searchQuery);
    setSearching(true);

    const sigv4 = new SignatureV4({
      service: "lambda",
      region: creds.identityId.split(":")[0],
      credentials: creds,
      sha256: Sha256
    });

    // Choose endpoint based on RAG mode
    const selectedInferenceURL = isGraphRAG ? appConfig.graphRagInferenceURL : inferenceURL;
    
    let apiUrl;
    if (streaming) {
      apiUrl = new URL(selectedInferenceURL);
    }
    else {
      apiUrl = new URL(selectedInferenceURL);
    }

    const promptOverride = getPromptOverrideObject(systemPrompt);

    const requestBody = {
      query: searchQuery,
      promptOverride,
      strategy: isGraphRAG ? "graphrag" : "rag",
      model: model,
      idToken: creds.idToken.toString(),
      history: getHistoryForConverseAPI()
    };

    console.log('Request body:', requestBody);
    console.log('Using endpoint:', selectedInferenceURL);

    try {
      const signed = await sigv4.sign({
        body: JSON.stringify(requestBody),
        method: "POST",
        hostname: apiUrl.hostname,
        path: apiUrl.pathname,
        protocol: apiUrl.protocol,
        headers: {
          "Content-Type": "application/json",
          host: apiUrl.hostname
        }
      });

      if (streaming) {
        await streamingLambda(
          apiUrl.origin,
          signed.method,
          signed.headers,
          requestBody, 
          (value) => { setResults((data) => [...data, value]); }, 
          setMetadata
        );
      }
      else {
        await syncLambda(apiUrl.origin, "POST", requestBody, (value) => { setResults([value.message]); });
      }

      setSearching(false);

    } catch (error) {
      console.error("Error streaming data: ", error);
      setSearching(false);
    }
  };

  // Rest of your existing useEffect and other functions remain the same...
  useEffect(() => {
    if (results?.length > 0) {
      setResponseToLastQuestionInChatHistory(results?.join(""));
    }
  }, [results]);

  const getModelsFromBedrock = async () => {
    const bedrockClient = new BedrockClient({
      region: region,
      credentials: {
        accessKeyId: creds.accessKeyId,
        secretAccessKey: creds.secretAccessKey,
        sessionToken: creds.sessionToken,
      }
    });
  
    const command = new ListFoundationModelsCommand({});
    let models;
    try {
      models = await bedrockClient.send(command);
      return models.modelSummaries.filter(model => model.inferenceTypesSupported.includes('ON_DEMAND') && model.outputModalities.includes('TEXT'))
      .map(model => ({
        label: model.modelId,
        value: model.modelId,
      }));
    } catch (error) {
      console.warn("Error listing models: ", error);
      throw error;
    }
  };

  useEffect(() => {
    if (creds.accessKeyId) {
      getModelsFromBedrock().then(models => {
        setModels(models);
        const localStorageModel = localStorage.getItem('llm_model_id');
        if (localStorageModel) {
          setModel(localStorageModel);
        } else {
          setModel(models[0].value)
          localStorage.setItem('llm_model_id', models[0].value);
        }
      }).catch(error => {
        console.error("Error fetching models: ", error);
        setModels([{ value: 'none', label: 'Failed to load models' }]);
        setModel('none')
      });
    }
  }, [creds]);
    

  return (
    <Container header={
      <Header
      variant="h1"
    >
      Ask a question
    </Header>
    }>
      <SpaceBetween direction="vertical" size="m">
        <FormField
          label="RAG Mode"
          description="Choose between regular RAG (vector search) and GraphRAG (knowledge graph traversal)"
        >
          <Box>
            <Toggle
              onChange={({ detail }) => setIsGraphRAG(detail.checked)}
              checked={isGraphRAG}
            >
              <Box display="inline">
                {isGraphRAG ? (
                  <>
                    <Badge color="blue">GraphRAG</Badge> - Knowledge Graph & Vector Search
                  </>
                ) : (
                  <>
                    <Badge color="green">Regular RAG</Badge> - Vector Search Only
                  </>
                )}
              </Box>
            </Toggle>
          </Box>
        </FormField>

        <FormField
          label="LLM Model"
        >
          <Select
            selectedOption={{ label: model, value: model }}
            onChange={(event) => {
                setModel(event.detail.selectedOption.value);
                localStorage.setItem('llm_model_id', event.detail.selectedOption.value);
              }
            }
            options={models}
          />
        </FormField>

        { 
          systemPrompt?.isModified 
            && 
          <Alert statusIconAriaLabel="Info"> 
            You have modified the system prompt. You can switch back to the default prompt by navigating to <Link onFollow={() => navigate("/Settings")}>System Prompt Settings</Link>
          </Alert>
        }

        {
          chatHistory.some( item => item.checked) &&
          <Alert statusIconAriaLabel="Info">
            Some of your Chat History will be forwarded to the inference endpoint. You can manage your ChatHistory by navigating to <Link onFollow={() => navigate("/ChatHistory")}>Chat&nbsp;History</Link>
          </Alert>
        }

        {
          isGraphRAG &&
          <Alert statusIconAriaLabel="Info">
            GraphRAG mode uses both knowledge graph traversal and vector search to provide more comprehensive answers. Make sure you have uploaded documents to the GraphRAG collection via the <Link onFollow={() => navigate("/Documents")}>Documents</Link> page.
          </Alert>
        }

        <Textarea onChange={({ detail }) => setSearchQuery(detail.value)} value={searchQuery}></Textarea>
        
        <div>
          <Button 
            disabled={searchQuery.length===0 && model !== 'none'} 
            variant="primary" 
            iconName="search" 
            loading={searching} 
            onClick={() => getData(true)}
          >
            {isGraphRAG ? 'Submit GraphRAG Question' : 'Submit Question'}
          </Button>
        </div>

        <div className="qa_container">
          <div>
            <b>Question:</b> {searchQuery}
          </div>
          <div>
            <b>Response ({isGraphRAG ? 'GraphRAG' : 'Regular RAG'}):</b> 

            <Markdown
              rehypePlugins={[rehypeRaw]}
              children={results.join('')}
              components={{
                code(props) {
                  let {children, className, node, ...rest} = props
                  className = className && className.toLowerCase();
                  const match = /language-(\w+)/.exec(className || '');
                  return match ? (
                    <SyntaxHighlighter
                      {...rest}
                      PreTag="div"
                      children={String(children).replace(/\n$/, '')}
                      language={match[1]}
                      style={darkMarkdown}
                    />
                  ) : (
                    <code {...rest} className={className}>
                      {children}
                    </code>
                  )
                }
              }}
            />
            
          </div>
          <div>
            <b>Metadata:</b> {metadata.map((x) => <MetadataItem key={x.metadata.id || x.metadata.source} metadataItem={x} signer={getPresignedUrlAndRedirect} isGraphRAG={isGraphRAG} />)}
          </div>
        </div>
      </SpaceBetween>
    </Container>
  );
}

QAManager.propTypes = {
  models: PropTypes.arrayOf(PropTypes.shape({
    value: PropTypes.string
  })),
  inferenceURL: PropTypes.string.isRequired,
  creds: PropTypes.object.isRequired,
  region: PropTypes.string.isRequired,
  appConfig: PropTypes.object.isRequired,
};

function MetadataItem({metadataItem, signer, isGraphRAG}){
  const {metadata, content} = metadataItem;
  
  // Handle different metadata structures for regular RAG vs GraphRAG
  if (isGraphRAG && metadata.type === 'graphrag') {
    return (
      <div>
        <div><b>Type:</b> <Badge color="blue">Knowledge Graph</Badge></div>
        <div><b>Source:</b> {metadata.source}</div>
        {metadata.entities && <div><b>Entities:</b> {metadata.entities}</div>}
        {metadata.relationships && <div><b>Relationships:</b> {metadata.relationships}</div>}
        {metadata.tenant_id && <div><b>Tenant:</b> {metadata.tenant_id}</div>}
        <div><b>Content:</b> <ExpandableText>{content}</ExpandableText></div>
      </div>
    );
  }

  // Regular RAG metadata (existing logic)
  const {id, source, page} = metadata;
  const cleanSource = source.replace('/tmp/', '');
  const displaySource = source.split('/').pop();

  return (
    <div>
      <div><b>Type:</b> <Badge color="green">Vector Search</Badge></div>
      <div><b>ID:</b> {id}</div>
      <div><b>Source:</b> <Link onFollow={() => `${signer(cleanSource, parseInt(page)+1)}#page=${page}`}>{displaySource}</Link></div>
      <div><b>Page:</b> {page}</div>
      <div><b>Content:</b> <ExpandableText>{content}</ExpandableText></div>
    </div>
  );
}

MetadataItem.propTypes = {
  metadataItem: PropTypes.shape({
    metadata: PropTypes.object,
    content: PropTypes.string
  }),
  signer: PropTypes.func.isRequired,
  isGraphRAG: PropTypes.bool
}

const ExpandableText = ({ children }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const text = children || '';
  const previewText = text.slice(0, 140);
  
  const handleToggle = () => {
    setIsExpanded(!isExpanded);
  };

  return (
    <Box>
      {isExpanded ? (
        <>
          {text} <Button onClick={handleToggle} variant="link">[x]</Button>
        </>
      ) : (
        <>
          {previewText}{text.length > 140 && '...'} <Button onClick={handleToggle} variant="link">...</Button>
        </>
      )}
    </Box>
  );
};

ExpandableText.propTypes = {
  children: PropTypes.string
};
import * as React from "react";
import { FileViewTable } from "../FileViewTable/index"
import { S3Client, GetObjectCommand, ListObjectsV2Command, DeleteObjectCommand } from "@aws-sdk/client-s3";
import { useState, useEffect, useCallback } from 'react'; // Add useCallback
import { fetchAuthSession } from 'aws-amplify/auth';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

import {
    withAuthenticator,
  } from '@aws-amplify/ui-react';

import {
    Container,
    Tabs,
    Header,
    Box,
    Badge
} from '@cloudscape-design/components'

function Documents({ signOut, user, appConfig }) {
  const [remoteFiles, setRemoteFiles] = useState([]);
  const [remoteFilesLoading, setRemoteFilesLoading] = useState(false);
  const [graphRagFiles, setGraphRagFiles] = useState([]);
  const [graphRagFilesLoading, setGraphRagFilesLoading] = useState(false);
  const [creds, setCreds] = useState({});
  const [activeTabId, setActiveTabId] = useState("regular-rag");

  window.getAuthSession = fetchAuthSession;

  const getPresignedUrlAndRedirect = async (objectKey, isGraphRAGMode = false) => {
    const bucketName = isGraphRAGMode ? appConfig.graphRagStorage.bucket_name : appConfig.storage.bucket_name;

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
      window.open(signedUrl, '_blank');
    } catch (error) {
      console.error('Error generating pre-signed URL', error);
    }
  };

  const deleteFiles = async (files, isGraphRAGMode = false) => {
    const file = files[0];
    const bucketName = isGraphRAGMode ? appConfig.graphRagStorage.bucket_name : appConfig.storage.bucket_name;
    
    const s3Client = new S3Client({
      region: appConfig.storage.aws_region,
      credentials: {
        accessKeyId: creds.accessKeyId,
        secretAccessKey: creds.secretAccessKey,
        sessionToken: creds.sessionToken,
      }
    });

    const command = new DeleteObjectCommand({
      Bucket: bucketName,
      Key: file.Key,
    });

    await s3Client.send(command);
    
    if (isGraphRAGMode) {
      await listGraphRagObjects();
    } else {
      await listObjects();
    }
  }

  // MEMOIZE these functions to prevent infinite loops
  const listObjects = useCallback(async () => {
    if (creds.accessKeyId){
      setRemoteFilesLoading(true);

      const s3Client = new S3Client({
        region: appConfig.storage.aws_region,
        credentials: {
          accessKeyId: creds.accessKeyId,
          secretAccessKey: creds.secretAccessKey,
          sessionToken: creds.sessionToken,
        }
      });
  
      const params = {
        Bucket: appConfig.storage.bucket_name,
        Prefix: `private/${creds.identityId}`,
      };
      const command = new ListObjectsV2Command(params);
      let response
      try {
        response = await s3Client.send(command);
      } catch (error) {
        console.warn("Error listing objects: ", error);
      }
  
      setRemoteFiles(response?.Contents || []);
      setRemoteFilesLoading(false);
  
      return response;
    }
  }, [creds.accessKeyId, creds.secretAccessKey, creds.sessionToken, creds.identityId, appConfig.storage.aws_region, appConfig.storage.bucket_name]);

  const listGraphRagObjects = useCallback(async () => {
    if (creds.accessKeyId){
      setGraphRagFilesLoading(true);

      const s3Client = new S3Client({
        region: appConfig.graphRagStorage.aws_region,
        credentials: {
          accessKeyId: creds.accessKeyId,
          secretAccessKey: creds.secretAccessKey,
          sessionToken: creds.sessionToken,
        }
      });
  
      const params = {
        Bucket: appConfig.graphRagStorage.bucket_name,
        Prefix: `private/${creds.identityId}`,
      };
      const command = new ListObjectsV2Command(params);
      let response
      try {
        response = await s3Client.send(command);
      } catch (error) {
        console.warn("Error listing GraphRAG objects: ", error);
      }
  
      setGraphRagFiles(response?.Contents || []);
      setGraphRagFilesLoading(false);
  
      return response;
    }
  }, [creds.accessKeyId, creds.secretAccessKey, creds.sessionToken, creds.identityId, appConfig.graphRagStorage.aws_region, appConfig.graphRagStorage.bucket_name]);

  // Getting STS credentials for user
  useEffect(() => {
    const getSession = async () => {
      try {
        const { credentials, identityId, tokens } = await fetchAuthSession();
        setCreds({
          ...credentials,
          ...tokens,
          identityId
        });
      } catch (error) {
        console.error("Error fetching session: ", error);
      }
    };
    getSession();
  }, [user]);

  const tabs = [
    {
      label: (
        <Box display="inline">
          <Badge color="green">Regular RAG</Badge> Documents
        </Box>
      ),
      id: "regular-rag",
      content: (
        <FileViewTable
          tableItems={remoteFiles}
          loading={remoteFilesLoading}
          loader={listObjects}
          download={(objectKey) => getPresignedUrlAndRedirect(objectKey, false)}
          deleteFiles={(files) => deleteFiles(files, false)}
          creds={creds}
          acceptedFileTypes={['.pdf']}
          bucketConfig={{
            bucketName: appConfig.storage.bucket_name,
            region: appConfig.storage.aws_region
          }}
          ragMode="regular"
        />
      )
    },
    {
      label: (
        <Box display="inline">
          <Badge color="blue">GraphRAG</Badge> Documents
        </Box>
      ),
      id: "graphrag",
      content: (
        <FileViewTable
          tableItems={graphRagFiles}
          loading={graphRagFilesLoading}
          loader={listGraphRagObjects}
          download={(objectKey) => getPresignedUrlAndRedirect(objectKey, true)}
          deleteFiles={(files) => deleteFiles(files, true)}
          creds={creds}
          acceptedFileTypes={['.pdf', '.txt']}
          bucketConfig={{
            bucketName: appConfig.graphRagStorage.bucket_name,
            region: appConfig.graphRagStorage.aws_region
          }}
          ragMode="graphrag"
        />
      )
    }
  ];

  return (
    <Container header={<Header variant="h1">Document Management</Header>}>
      <Tabs
        tabs={tabs}
        activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
      />
    </Container>
  );
}

export default withAuthenticator(Documents);
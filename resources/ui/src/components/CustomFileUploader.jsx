import React, { useState } from 'react';
import { PropTypes } from 'prop-types';
import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import {
  Button,
  ProgressBar,
  Box,
  SpaceBetween,
  Alert
} from '@cloudscape-design/components';

export const CustomFileUploader = ({ 
  acceptedFileTypes, 
  bucketConfig, 
  creds, 
  onUploadComplete,
  isGraphRAG = false
}) => {
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState(null);

  const handleFileSelect = (event) => {
    const file = event.target.files[0];
    if (file) {
      const fileExtension = '.' + file.name.split('.').pop().toLowerCase();
      if (acceptedFileTypes.includes(fileExtension)) {
        setSelectedFile(file);
        setError(null);
      } else {
        setError(`File type ${fileExtension} not supported. Accepted types: ${acceptedFileTypes.join(', ')}`);
        setSelectedFile(null);
      }
    }
  };

  const uploadFile = async () => {
    if (!selectedFile || !creds.identityId) return;

    setUploading(true);
    setUploadProgress(0);
    setError(null);

    try {
      const s3Client = new S3Client({
        region: bucketConfig.region,
        credentials: {
          accessKeyId: creds.accessKeyId,
          secretAccessKey: creds.secretAccessKey,
          sessionToken: creds.sessionToken,
        }
      });

      const key = `private/${creds.identityId}/${selectedFile.name}`;
      
      const command = new PutObjectCommand({
        Bucket: bucketConfig.bucketName,
        Key: key,
        Body: selectedFile,
        ContentType: selectedFile.type,
      });

      await s3Client.send(command);
      
      setUploadProgress(100);
      setSelectedFile(null);
      
      if (onUploadComplete) {
        onUploadComplete();
      }
      
      // Reset file input
      const fileInput = document.getElementById('file-upload-input');
      if (fileInput) fileInput.value = '';
      
    } catch (err) {
      console.error('Upload error:', err);
      setError('Failed to upload file: ' + err.message);
    } finally {
      setUploading(false);
      setTimeout(() => setUploadProgress(0), 2000);
    }
  };

  const acceptedTypesString = acceptedFileTypes.join(',');

  return (
    <SpaceBetween size="m" direction="vertical">
      {error && (
        <Alert type="error">
          {error}
        </Alert>
      )}
      
      <Box>
        <input
          id="file-upload-input"
          type="file"
          accept={acceptedTypesString}
          onChange={handleFileSelect}
          style={{ display: 'none' }}
        />
        <Button
          onClick={() => document.getElementById('file-upload-input').click()}
          disabled={uploading}
        >
          Browse Files
        </Button>
      </Box>

      {selectedFile && (
        <Box>
          <strong>Selected:</strong> {selectedFile.name} ({Math.round(selectedFile.size / 1024)} KB)
        </Box>
      )}

      {selectedFile && !uploading && (
        <Button
          variant="primary"
          onClick={uploadFile}
          disabled={!selectedFile}
        >
          Upload to {isGraphRAG ? 'GraphRAG' : 'Regular RAG'}
        </Button>
      )}

      {uploading && (
        <ProgressBar
          value={uploadProgress}
          additionalInfo="Uploading..."
          description={selectedFile?.name}
        />
      )}
    </SpaceBetween>
  );
};

CustomFileUploader.propTypes = {
  acceptedFileTypes: PropTypes.arrayOf(PropTypes.string).isRequired,
  bucketConfig: PropTypes.shape({
    bucketName: PropTypes.string.isRequired,
    region: PropTypes.string.isRequired
  }).isRequired,
  creds: PropTypes.object.isRequired,
  onUploadComplete: PropTypes.func,
  isGraphRAG: PropTypes.bool
};
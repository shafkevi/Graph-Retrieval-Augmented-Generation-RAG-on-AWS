import React, { useState, useEffect } from 'react';
import {
  Box,
  SpaceBetween,
  Checkbox,
  Button,
  Input,
  Header,
  Container,
  Textarea,
  Badge
} from '@cloudscape-design/components';

const ChatHistoryComponent = () => {
  const [chatHistory, setChatHistory] = useState([]);
  const [displayedItems, setDisplayedItems] = useState([]);
  const [itemsToLoad, setItemsToLoad] = useState(5);
  const [isLoading, setIsLoading] = useState(true);

  // Load chat history once on component mount
  useEffect(() => {
    const loadChatHistory = () => {
      try {
        setIsLoading(true);
        const storedChatHistory = localStorage.getItem('chat_history');
        if (storedChatHistory) {
          const parsedChatHistory = JSON.parse(storedChatHistory);
          // Ensure it's an array and has valid structure
          if (Array.isArray(parsedChatHistory)) {
            const validatedHistory = parsedChatHistory.map(item => ({
              question: item.question || '',
              answer: item.answer || '',
              checked: Boolean(item.checked),
              date: item.date || new Date().toISOString(),
              model: item.model || '',
              ragType: item.ragType || 'regular'
            }));
            setChatHistory(validatedHistory);
          } else {
            console.warn('Invalid chat history format, resetting to empty array');
            setChatHistory([]);
            localStorage.setItem('chat_history', JSON.stringify([]));
          }
        } else {
          setChatHistory([]);
        }
      } catch (error) {
        console.error("Error loading chat history:", error);
        setChatHistory([]);
        // Clear corrupted data
        localStorage.removeItem('chat_history');
      } finally {
        setIsLoading(false);
      }
    };

    loadChatHistory();
  }, []);

  // Update displayed items whenever chatHistory or itemsToLoad changes
  useEffect(() => {
    if (Array.isArray(chatHistory)) {
      setDisplayedItems(chatHistory.slice(0, itemsToLoad));
    }
  }, [chatHistory, itemsToLoad]);

  // Utility function to update localStorage safely
  const updateLocalStorage = (newHistory) => {
    try {
      localStorage.setItem('chat_history', JSON.stringify(newHistory));
    } catch (error) {
      console.error('Error updating localStorage:', error);
    }
  };

  const handleQuestionChange = (index, value) => {
    setChatHistory(prevHistory => {
      const updatedHistory = [...prevHistory];
      updatedHistory[index] = { ...updatedHistory[index], question: value };
      updateLocalStorage(updatedHistory);
      return updatedHistory;
    });
  };

  const handleAnswerChange = (index, value) => {
    setChatHistory(prevHistory => {
      const updatedHistory = [...prevHistory];
      updatedHistory[index] = { ...updatedHistory[index], answer: value };
      updateLocalStorage(updatedHistory);
      return updatedHistory;
    });
  };

  const handleSelectAll = (select) => {
    setChatHistory(prevHistory => {
      const updatedHistory = prevHistory.map((item) => ({
        ...item,
        checked: select,
      }));
      updateLocalStorage(updatedHistory);
      return updatedHistory;
    });
  };

  const handleCheckboxChange = (index, checked) => {
    setChatHistory(prevHistory => {
      const updatedHistory = [...prevHistory];
      updatedHistory[index] = { ...updatedHistory[index], checked };
      updateLocalStorage(updatedHistory);
      return updatedHistory;
    });
  };

  const handleDelete = (index) => {
    setChatHistory(prevHistory => {
      const updatedHistory = prevHistory.filter((_, i) => i !== index);
      updateLocalStorage(updatedHistory);
      return updatedHistory;
    });
  };

  const loadMoreItems = () => {
    setItemsToLoad((prev) => prev + 5);
  };

  const addNewItem = () => {
    const newItem = { 
      question: '', 
      answer: '', 
      checked: false,
      date: new Date().toISOString(),
      model: '',
      ragType: 'regular'
    };
    
    setChatHistory(prevHistory => {
      const updatedHistory = [newItem, ...prevHistory];
      updateLocalStorage(updatedHistory);
      return updatedHistory;
    });
  };

  const downloadHistory = () => {
    try {
      const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(chatHistory, null, 2));
      const downloadAnchorNode = document.createElement('a');
      downloadAnchorNode.setAttribute("href", dataStr);
      downloadAnchorNode.setAttribute("download", "chat_history.json");
      document.body.appendChild(downloadAnchorNode);
      downloadAnchorNode.click();
      downloadAnchorNode.remove();
    } catch (error) {
      console.error('Error downloading history:', error);
    }
  };

  if (isLoading) {
    return (
      <Container header={<Header variant="h1">Manage Chat History</Header>}>
        <Box textAlign="center" padding="xxl">
          Loading chat history...
        </Box>
      </Container>
    );
  }

  return (
    <Container header={<Header variant="h1">Manage Chat History</Header>}>
      <ul>
        <li>Selected messages are included in the history of the chat.</li>
        <li>You can add new messages for debugging purposes.</li>
        <li>Messages are sorted in reverse chronological order, with the most recent message on top.</li>
      </ul>
      <Box padding="m">
        <SpaceBetween size="m" direction="vertical">
          <Box direction="horizontal" alignItems="center" justifyContent="space-between">
            <Button onClick={addNewItem}>Add New Item</Button>
            <Box>
              <Button onClick={() => handleSelectAll(true)}>Select All</Button>
              <Button onClick={() => handleSelectAll(false)}>Deselect All</Button>
              {chatHistory && chatHistory.length > 0 && (
                <Button onClick={downloadHistory}>Download History</Button>
              )}
            </Box>
          </Box>
          {displayedItems && displayedItems.map((item, index) => (
            <Box key={`${item.date}-${index}`} padding={{ vertical: 's' }} border={{ color: 'black', style: 'solid' }}>
              <Box direction="horizontal" alignItems="center" justifyContent="space-between">
                <Box direction="horizontal" alignItems="center">
                  <Checkbox
                    checked={item.checked || false}
                    onChange={({ detail }) =>
                      handleCheckboxChange(index, detail.checked)
                    }
                  >
                    {item.date} via <em>{item.model ? item.model : "User Input"}</em>
                    {item.ragType && (
                      <Badge color={item.ragType === 'graphrag' ? 'blue' : 'green'}>
                        {item.ragType === 'graphrag' ? 'GraphRAG' : 'Regular RAG'}
                      </Badge>
                    )}
                  </Checkbox>
                </Box>
                <Button onClick={() => handleDelete(index)}>Delete</Button>
              </Box>
              <SpaceBetween size="s" direction="vertical">
                <Input
                  value={item.question || ''}
                  onChange={({ detail }) =>
                    handleQuestionChange(index, detail.value)
                  }
                  placeholder="Question"
                />
                <Textarea
                  value={item.answer || ''}
                  onChange={({ detail }) =>
                    handleAnswerChange(index, detail.value)
                  }
                  placeholder="Answer"
                />
              </SpaceBetween>
            </Box>
          ))}
          {chatHistory && itemsToLoad < chatHistory.length && (
            <Button onClick={loadMoreItems}>Load More</Button>
          )}
        </SpaceBetween>
      </Box>
    </Container>
  );
};

export default ChatHistoryComponent;
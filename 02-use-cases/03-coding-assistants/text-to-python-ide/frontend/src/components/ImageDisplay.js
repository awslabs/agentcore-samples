import React, { memo } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  ColumnLayout,
  Badge
} from '@cloudscape-design/components';

const ImageDisplay = memo(({ images = [] }) => {
  if (!images || images.length === 0) {
    return null;
  }

  const downloadImage = (imageData, index) => {
    try {
      const link = document.createElement('a');
      link.href = `data:image/png;base64,${imageData}`;
      link.download = `chart_${index + 1}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('Failed to download image:', error);
    }
  };

  return (
    <Container header={<Header variant="h3">Generated Charts & Visualizations</Header>}>
      <SpaceBetween direction="vertical" size="m">
        {images.length === 1 ? (
          // Single image - full width
          <Box>
            <SpaceBetween direction="vertical" size="s">
              <Box display="flex" justifyContent="space-between" alignItems="center">
                <Badge color="blue">Chart 1</Badge>
                <Button
                  variant="link"
                  iconName="download"
                  onClick={() => downloadImage(images[0].data, 0)}
                >
                  Download PNG
                </Button>
              </Box>
              <Box textAlign="center">
                <img
                  src={`data:image/png;base64,${images[0].data}`}
                  alt="Generated Chart 1"
                  style={{
                    maxWidth: '100%',
                    height: 'auto',
                    border: '1px solid #e9ebed',
                    borderRadius: '8px',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
                  }}
                />
              </Box>
            </SpaceBetween>
          </Box>
        ) : (
          // Multiple images - grid layout
          <ColumnLayout columns={images.length > 2 ? 2 : images.length}>
            {images.map((image, index) => (
              <Box key={index}>
                <SpaceBetween direction="vertical" size="s">
                  <Box display="flex" justifyContent="space-between" alignItems="center">
                    <Badge color="blue">Chart {index + 1}</Badge>
                    <Button
                      variant="link"
                      iconName="download"
                      onClick={() => downloadImage(image.data, index)}
                    >
                      Download PNG
                    </Button>
                  </Box>
                  <Box textAlign="center">
                    <img
                      src={`data:image/png;base64,${image.data}`}
                      alt={`Generated Chart ${index + 1}`}
                      style={{
                        maxWidth: '100%',
                        height: 'auto',
                        border: '1px solid #e9ebed',
                        borderRadius: '8px',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
                      }}
                    />
                  </Box>
                </SpaceBetween>
              </Box>
            ))}
          </ColumnLayout>
        )}
        
        <Box fontSize="body-s" color="text-body-secondary" textAlign="center">
          {images.length === 1 
            ? 'Chart generated from your Python code execution'
            : `${images.length} charts generated from your Python code execution`
          }
        </Box>
      </SpaceBetween>
    </Container>
  );
});

ImageDisplay.displayName = 'ImageDisplay';

export default ImageDisplay;

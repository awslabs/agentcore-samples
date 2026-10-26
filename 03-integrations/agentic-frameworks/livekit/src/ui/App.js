import React, { useState, useEffect, useRef } from 'react';
import { LiveKitRoom, AudioConference, useRoomContext, useTrackTranscription } from '@livekit/components-react';
import '@livekit/components-styles';
import './App.css';
import axios from 'axios';

const WS_URL = process.env.REACT_APP_LIVEKIT_SERVER_URL || "wss://livekit.drishtic.ai";
const API_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

function App() {
  const [token, setToken] = useState(null);
  const [roomName, setRoomName] = useState('');
  const [candidateName, setCandidateName] = useState('');
  const agentType = 'agentcore'; // Always use agentcore
  const [isJoining, setIsJoining] = useState(false);
  const [error, setError] = useState(null);
  const [transcript, setTranscript] = useState([]);
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const generateToken = async () => {
    if (!candidateName.trim()) {
      setError('Please enter your name');
      return;
    }

    setIsJoining(true);
    setError(null);

    try {
      const response = await axios.post(`${API_URL}/generate-token`, {
        participant_name: candidateName,
        room_name: roomName || `interview-${Date.now()}`,
        agent_type: agentType
      });

      setToken(response.data.token);
      setRoomName(response.data.room_name);
    } catch (err) {
      setError('Failed to connect. Please try again.');
      console.error('Token generation error:', err);
      setIsJoining(false);
    }
  };

  const handleDisconnect = async () => {
    // Try to get session ID from room name if not received via data channel
    if (!sessionId && roomName) {
      try {
        const response = await axios.get(`${API_URL}/session-by-room/${roomName}`);
        setSessionId(response.data.session_id);
      } catch (err) {
        console.error('Failed to fetch session ID:', err);
      }
    }
    setShowFeedback(true);
  };

  const fetchFeedback = async () => {
    if (!sessionId) {
      alert('Session ID not available. Please wait.');
      return;
    }
    try {
      const response = await axios.get(`${API_URL}/feedback/${sessionId}`);
      // Open PDF in new tab
      window.open(response.data.pdf_url, '_blank');
      setFeedback(response.data);
    } catch (err) {
      console.error('Failed to fetch feedback:', err);
      alert(err.response?.data?.detail || 'Feedback not ready yet. Please wait a moment and try again.');
    }
  };

  const resetInterview = () => {
    setToken(null);
    setCandidateName('');
    setRoomName('');
    setIsJoining(false);
    setTranscript([]);
    setShowFeedback(false);
    setFeedback(null);
    setSessionId(null);
  };

  const CandidateVideo = () => {
    const room = useRoomContext();
    const videoStreamRef = useRef(null);

    useEffect(() => {
      if (!room) return;

      const setupVideo = async () => {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
          videoStreamRef.current = stream;
          const videoElement = document.getElementById('candidate-video');
          if (videoElement) {
            videoElement.srcObject = stream;
          }
        } catch (err) {
          console.error('Failed to get video stream:', err);
        }
      };
      setupVideo();

      const handleDataReceived = (payload, participant, kind) => {
        try {
          const decoder = new TextDecoder();
          const text = decoder.decode(payload);
          const data = JSON.parse(text);
          if (data.type === 'session_id') {
            setSessionId(data.session_id);
            console.log('Session ID received:', data.session_id);
          }
        } catch (e) {
          console.error('Error parsing data:', e);
        }
      };

      // Detect agent speaking
      const handleTrackSubscribed = (track, publication, participant) => {
        if (track.kind === 'audio' && participant.identity === 'agentcore-agent') {
          track.on('audioPlaybackStarted', () => setIsSpeaking(true));
          track.on('audioPlaybackFailed', () => setIsSpeaking(false));
        }
      };

      const handleActiveSpeakersChanged = (speakers) => {
        const agentSpeaking = speakers.some(s => s.identity === 'agentcore-agent');
        setIsSpeaking(agentSpeaking);
      };

      room.on('data', handleDataReceived);
      room.on('trackSubscribed', handleTrackSubscribed);
      room.on('activeSpeakersChanged', handleActiveSpeakersChanged);

      return () => {
        room.off('data', handleDataReceived);
        room.off('trackSubscribed', handleTrackSubscribed);
        room.off('activeSpeakersChanged', handleActiveSpeakersChanged);
        
        // Stop video stream when component unmounts
        if (videoStreamRef.current) {
          videoStreamRef.current.getTracks().forEach(track => track.stop());
        }
      };
    }, [room]);

    return null;
  };

  if (showFeedback) {
    return (
      <div className='livekit'>
        <div className='header'>
          <div className='title'>Interview Complete</div>
          <div className='subtitle'>Thank you, {candidateName}!</div>
        </div>
        <div className='transcript-complete'>
          <h2>📊 Your Interview Feedback</h2>
          {!feedback ? (
            <div>
              <p>Your interview has been evaluated by AI. Click below to view your feedback report.</p>
              <p style={{fontSize: '0.9em', color: '#666'}}>Session ID: {sessionId || 'Loading...'}</p>
              <button onClick={fetchFeedback} className='join-btn' disabled={!sessionId}>View Feedback PDF</button>
            </div>
          ) : (
            <div>
              <div className='feedback-summary'>
                <h3>✅ Feedback Report Ready</h3>
                <p>Your feedback PDF has been opened in a new tab.</p>
                <p><strong>Filename:</strong> {feedback.filename}</p>
                <button onClick={fetchFeedback} className='join-btn' style={{marginTop: '15px'}}>Open PDF Again</button>
              </div>
            </div>
          )}
          <button onClick={resetInterview} className='join-btn' style={{marginTop: '20px'}}>Start New Interview</button>
        </div>
      </div>
    );
  }

  if (token) {
    return (
      <div className='livekit'>
        <div className='header'>
          <div className='title'>AI Interview - Behavioral Assessment</div>
          <div className='subtitle'>Powered by AWS Bedrock Nova Sonic</div>
        </div>
        <div className='interview-info'>
          <span>Candidate: {candidateName}</span>
          <button onClick={handleDisconnect} className='disconnect-btn'>End Interview</button>
        </div>
        <LiveKitRoom 
          audio={true} 
          video={true} 
          token={token} 
          serverUrl={WS_URL} 
          connect={true}
          onDisconnected={handleDisconnect}
        >
          <CandidateVideo />
          <div className='video-panels'>
            <div className='video-panel candidate-panel'>
              <h3>You</h3>
              <video id='candidate-video' autoPlay playsInline muted />
            </div>
            <div className='video-panel agent-panel'>
              <h3>Lexis - AI Interviewer</h3>
              <div className='aws-logo-container'>
                <img src='https://upload.wikimedia.org/wikipedia/commons/9/93/Amazon_Web_Services_Logo.svg' alt='AWS' className={`aws-logo ${isSpeaking ? 'speaking' : 'pulse'}`} />
              </div>
            </div>
          </div>
          <div style={{display: 'none'}}>
            <AudioConference />
          </div>
        </LiveKitRoom>
      </div>
    );
  }

  return (
    <div className='livekit'>
      <div className='header'>
        <div className='title'>AI Interview Agent</div>
        <div className='subtitle'>Behavioral Interview Assessment</div>
      </div>
      
      <div className='join-form'>
        <h2>Welcome to Your Interview</h2>
        <p>You'll be interviewed on 4 behavioral competencies:</p>
        <ul>
          <li>Teamwork</li>
          <li>Problem Solving</li>
          <li>Communication</li>
          <li>Adaptability</li>
        </ul>
        
        <div className='form-group'>
          <label>Your Name:</label>
          <input
            type="text"
            value={candidateName}
            onChange={(e) => setCandidateName(e.target.value)}
            placeholder="Enter your full name"
            onKeyPress={(e) => e.key === 'Enter' && generateToken()}
          />
        </div>

        {error && <div className='error'>{error}</div>}

        <button 
          onClick={generateToken} 
          disabled={isJoining}
          className='join-btn'
        >
          {isJoining ? 'Connecting...' : 'Start Interview'}
        </button>

        <div className='instructions'>
          <p><strong>Instructions:</strong></p>
          <ul>
            <li>Ensure your camera and microphone are working</li>
            <li>Find a quiet place for the interview</li>
            <li>Speak clearly and wait for the AI to finish asking questions</li>
            <li>The interview will take approximately 20-30 minutes</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

export default App;

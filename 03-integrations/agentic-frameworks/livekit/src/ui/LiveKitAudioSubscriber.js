// LiveKitAudioSubscriber.jsx
import React from 'react';
import { Room, Participant, RemoteTrackPublication, RemoteAudioTrack } from 'livekit-client';

class LiveKitAudioSubscriber extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      room: null,
      connected: false,
    };
  }

  async componentDidMount() {
    const { token, serverUrl } = this.props;

    const room = new Room();
    room.on('trackSubscribed', this.handleTrackSubscribed);

    await room.connect(serverUrl, token);
    this.setState({ room, connected: true });
  }

  handleTrackSubscribed = (track, publication, participant) => {
    if (track.kind === 'audio') {
      const audioElement = track.attach();
      audioElement.autoplay = true;
      audioElement.controls = false;
      document.body.appendChild(audioElement); // Or mount to a specific DOM node
    }
  };

  componentWillUnmount() {
    const { room } = this.state;
    if (room) {
      room.disconnect();
    }
  }

  render() {
    return <div>{this.state.connected ? 'Connected to LiveKit' : 'Connecting...'}</div>;
  }
}

export default LiveKitAudioSubscriber;

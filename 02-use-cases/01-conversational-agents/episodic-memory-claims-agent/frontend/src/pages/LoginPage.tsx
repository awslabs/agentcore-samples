import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import './LoginPage.css';

const DEMO_USERS = [
  { username: 'bob-policyholder', label: 'Bob Thompson', initials: 'BT', color: '#4f8ff7' },
  { username: 'alice-policyholder', label: 'Alice Martinez', initials: 'AM', color: '#34d399' },
  { username: 'charlie-policyholder', label: 'Charlie Davis', initials: 'CD', color: '#fbbf24' },
  { username: 'david-policyholder', label: 'David Park', initials: 'DP', color: '#f97316' },
  { username: 'sarah-policyholder', label: 'Sarah Chen', initials: 'SC', color: '#ec4899' },
  { username: 'marcus-policyholder', label: 'Marcus Rivera', initials: 'MR', color: '#06b6d4' },
  { username: 'lisa-policyholder', label: 'Lisa Nguyen', initials: 'LN', color: '#8b5cf6' },
];

export default function LoginPage() {
  const { login, loading, error } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  return (
    <div className="login-page">
      <div className="login-bg" />
      <div className="login-card">
        <div className="login-header">
          <h1 className="login-title">Claims Agent</h1>
          <p className="login-subtitle">AI-powered claims with human-grounded memory</p>
        </div>

        <div className="login-section">
          <span className="login-section-label">Policy Holders</span>
          <div className="login-quick-users">
            {DEMO_USERS.map((u) => (
              <button
                key={u.username}
                className="login-quick-btn"
                onClick={() => login(u.username)}
                disabled={loading}
              >
                {u.label}
              </button>
            ))}
          </div>
        </div>

        <div className="login-section login-section-staff">
          <span className="login-section-label">Staff</span>
          <div className="login-quick-users">
            <button
              className="login-quick-btn login-staff-btn"
              onClick={() => login('dana-adjuster')}
              disabled={loading}
            >
              Dana Reyes (Adjuster)
            </button>
            <button
              className="login-quick-btn login-staff-btn"
              onClick={() => login('amy-admin')}
              disabled={loading}
            >
              Amy (Admin)
            </button>
          </div>
        </div>

        <div className="login-divider">
          <span>OR</span>
        </div>

        <form
          className="login-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (email) login(email, password);
          }}
        >
          <input
            className="login-input"
            type="text"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={loading}
          />
          <input
            className="login-input"
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
          />
          <button className="login-submit" type="submit" disabled={loading || !email}>
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        {error && <p className="login-error">{error}</p>}
      </div>
    </div>
  );
}

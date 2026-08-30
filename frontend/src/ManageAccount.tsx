import React, { useState } from 'react';
import { useAuth } from './AuthContext';
import api, { extractErrorMessage } from './api';
import SubjectsManager from './SubjectsManager';
import { AlertCircle, CheckCircle2 } from 'lucide-react';

const YEAR_GROUPS = ['Year 7', 'Year 8', 'Year 9', 'Year 10', 'Year 11', 'Year 12', 'Year 13'];

export default function ManageAccount() {
  const { user, login } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name || '');
  const [yearGroup, setYearGroup] = useState(user?.year_group || YEAR_GROUPS[0]);
  const [profileMsg, setProfileMsg] = useState('');
  const [profileError, setProfileError] = useState('');

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordMsg, setPasswordMsg] = useState('');
  const [passwordError, setPasswordError] = useState('');

  const handleProfileSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setProfileError('');
    setProfileMsg('');
    try {
      const res = await api.patch('/auth/me', {
        display_name: displayName,
        year_group: yearGroup || null,
      });
      await login('cookie-managed', res.data);
      setProfileMsg('Profile updated.');
    } catch (err: any) {
      setProfileError(extractErrorMessage(err, 'Failed to update profile'));
    }
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError('');
    setPasswordMsg('');
    if (newPassword !== confirmPassword) {
      setPasswordError('New passwords do not match');
      return;
    }
    try {
      await api.post('/auth/me/password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setPasswordMsg('Password updated.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: any) {
      setPasswordError(extractErrorMessage(err, 'Failed to update password'));
    }
  };

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '600px' }}>
      <h1 style={{ marginBottom: '2rem' }}>Manage Account</h1>

      <div className="card" style={{ marginBottom: '2rem' }}>
        <h3>Your subjects</h3>
        <div style={{ marginTop: '1rem' }}>
          <SubjectsManager />
        </div>
      </div>

      <div className="card" style={{ marginBottom: '2rem' }}>
        <h3>Profile information</h3>
        {profileError && (
          <div className="banner banner-danger" role="alert" style={{ marginTop: '1rem' }}>
            <AlertCircle size={16} />
            <span>{profileError}</span>
          </div>
        )}
        {profileMsg && (
          <div className="banner banner-success" role="status" style={{ marginTop: '1rem' }}>
            <CheckCircle2 size={16} />
            <span>{profileMsg}</span>
          </div>
        )}
        <form onSubmit={handleProfileSubmit} style={{ marginTop: '1rem' }}>
          <div className="form-group">
            <label htmlFor="account-email">Email address</label>
            <input id="account-email" type="email" value={user?.email || ''} className="form-control" disabled />
          </div>
          <div className="form-group">
            <label htmlFor="account-name">Full name</label>
            <input
              id="account-name"
              type="text"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              className="form-control"
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="account-year">Year group</label>
            <select id="account-year" value={yearGroup} onChange={e => setYearGroup(e.target.value)} className="form-control">
              {YEAR_GROUPS.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <button type="submit" className="btn btn-primary" style={{ marginTop: '0.5rem' }}>
            Save changes
          </button>
        </form>
      </div>

      <div className="card">
        <h3>Change password</h3>
        {passwordError && (
          <div className="banner banner-danger" role="alert" style={{ marginTop: '1rem' }}>
            <AlertCircle size={16} />
            <span>{passwordError}</span>
          </div>
        )}
        {passwordMsg && (
          <div className="banner banner-success" role="status" style={{ marginTop: '1rem' }}>
            <CheckCircle2 size={16} />
            <span>{passwordMsg}</span>
          </div>
        )}
        <form onSubmit={handlePasswordSubmit} style={{ marginTop: '1rem' }}>
          <div className="form-group">
            <label htmlFor="account-current-password">Current password</label>
            <input
              id="account-current-password"
              type="password"
              value={currentPassword}
              onChange={e => setCurrentPassword(e.target.value)}
              className="form-control"
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="account-new-password">New password</label>
            <input
              id="account-new-password"
              type="password"
              value={newPassword}
              onChange={e => setNewPassword(e.target.value)}
              className="form-control"
              required
              minLength={8}
            />
          </div>
          <div className="form-group">
            <label htmlFor="account-confirm-password">Confirm new password</label>
            <input
              id="account-confirm-password"
              type="password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              className="form-control"
              required
              minLength={8}
            />
          </div>
          <button type="submit" className="btn btn-primary" style={{ marginTop: '0.5rem' }}>
            Update password
          </button>
        </form>
      </div>
    </div>
  );
}

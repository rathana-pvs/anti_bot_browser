import React, { useState, useEffect } from 'react';
import { Profile, ProfileCreateRequest, ProfileUpdateRequest, SystemStats } from './types/profile';
import { ProxyItem } from './types/proxy';
import {
  fetchProfiles,
  createProfile,
  updateProfile,
  executeProfileAction,
  deleteProfile,
  fetchSystemStats,
  fetchProxies,
} from './services/api';
import { Sidebar } from './components/Sidebar';
import { VncViewer } from './components/VncViewer';
import { ProfileDetails } from './components/ProfileDetails';
import { ProxyPanel } from './components/ProxyPanel';
import { CampaignsPanel } from './components/CampaignsPanel';
import { CreateProfileModal } from './components/CreateProfileModal';
import { EditProfileModal } from './components/EditProfileModal';
import { TelemetryFooter } from './components/TelemetryFooter';
import { BrainPanel } from './components/BrainPanel';
import { ProfileActionErrorModal } from './components/ProfileActionErrorModal';

type ProfileAction = 'start' | 'stop' | 'pause' | 'unpause';

interface ProfileActionError {
  profileId: string;
  profileName: string;
  action: ProfileAction;
  reason: string;
}

export const App: React.FC = () => {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [proxies, setProxies] = useState<ProxyItem[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'profiles' | 'proxies' | 'campaigns' | 'brains'>('profiles');
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [profileActionError, setProfileActionError] = useState<ProfileActionError | null>(null);
  const [isInspectorOpen, setIsInspectorOpen] = useState<boolean>(() => {
    const saved = localStorage.getItem('inspector_open');
    return saved !== null ? saved === 'true' : true;
  });

  const [isSidebarOpen, setIsSidebarOpen] = useState<boolean>(() => {
    const saved = localStorage.getItem('sidebar_open');
    return saved !== null ? saved === 'true' : true;
  });

  const toggleInspector = () => {
    setIsInspectorOpen((prev) => {
      const next = !prev;
      localStorage.setItem('inspector_open', String(next));
      return next;
    });
  };

  const toggleSidebar = () => {
    setIsSidebarOpen((prev) => {
      const next = !prev;
      localStorage.setItem('sidebar_open', String(next));
      return next;
    });
  };

  const [systemStats, setSystemStats] = useState<SystemStats>({
    docker_running: false,
    active_profiles: 0,
    total_profiles: 0,
    used_memory_mb: 0,
    total_memory_mb: 16384,
    available_proxies: 0,
  });

  const loadData = async () => {
    try {
      const [profilesData, statsData, proxiesData] = await Promise.all([
        fetchProfiles(),
        fetchSystemStats(),
        fetchProxies(),
      ]);
      setProfiles(profilesData);
      setSystemStats(statsData);
      setProxies(proxiesData);

      // Auto-select first profile ONLY if none is currently selected
      setSelectedProfileId((prev) => {
        if (!prev && profilesData.length > 0) {
          return profilesData[0].id;
        }
        // If currently selected profile was removed, fallback to first
        if (prev && !profilesData.some((p) => p.id === prev)) {
          return profilesData.length > 0 ? profilesData[0].id : null;
        }
        return prev;
      });
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
    }
  };

  const isModalOpenRef = React.useRef(false);
  isModalOpenRef.current = isCreateModalOpen || isEditModalOpen;

  useEffect(() => {
    loadData();
    const interval = setInterval(() => {
      if (!isModalOpenRef.current) {
        loadData();
      }
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const selectedProfile = profiles.find((p) => p.id === selectedProfileId) || null;

  const handleAction = async (profileId: string, action: ProfileAction) => {
    try {
      setProfileActionError(null);
      await executeProfileAction(profileId, action);
      await loadData();
    } catch (err: any) {
      const profile = profiles.find((item) => item.id === profileId);
      setProfileActionError({
        profileId,
        profileName: profile?.name || profileId,
        action,
        reason: err?.message || `Failed to ${action} profile`,
      });
      await loadData();
    }
  };

  const handleCreate = async (profileData: ProfileCreateRequest) => {
    try {
      const newProfile = await createProfile(profileData);
      await loadData();
      setSelectedProfileId(newProfile.id);
      setActiveTab('profiles');
    } catch (err: any) {
      alert(`Failed to create profile: ${err.message}`);
    }
  };

  const handleUpdate = async (profileId: string, updates: ProfileUpdateRequest) => {
    try {
      await updateProfile(profileId, updates);
      await loadData();
    } catch (err: any) {
      alert(`Failed to update profile: ${err.message}`);
    }
  };

  const handleDelete = async (profileId: string) => {
    try {
      await deleteProfile(profileId, true);
      if (selectedProfileId === profileId) {
        setSelectedProfileId(null);
      }
      await loadData();
    } catch (err: any) {
      alert(`Failed to delete profile: ${err.message}`);
    }
  };

  return (
    <div
      onContextMenu={(e) => e.preventDefault()}
      className="w-screen h-screen flex flex-col bg-background text-zinc-100 overflow-hidden font-sans"
    >
      {/* Main Layout Area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Profiles Sidebar with Tab Switcher */}
        <Sidebar
          profiles={profiles}
          selectedProfileId={selectedProfileId}
          activeTab={activeTab}
          onTabChange={setActiveTab}
          proxyCount={proxies.length}
          onSelectProfile={(p) => setSelectedProfileId(p.id)}
          onOpenCreateModal={() => setIsCreateModalOpen(true)}
          onAction={handleAction}
          isOpen={isSidebarOpen}
          onToggle={toggleSidebar}
        />

        {/* Center / Right Content Area */}
        <div className={`flex-1 flex overflow-hidden ${activeTab === 'proxies' ? '' : 'hidden'}`}>
          <ProxyPanel
            proxies={proxies}
            onRefresh={loadData}
            onSelectProfile={(profileId) => {
              setSelectedProfileId(profileId);
              setActiveTab('profiles');
            }}
            isSidebarOpen={isSidebarOpen}
            onToggleSidebar={toggleSidebar}
          />
        </div>

        <div className={`flex-1 flex overflow-hidden ${activeTab === 'campaigns' ? '' : 'hidden'}`}>
          <CampaignsPanel
            profiles={profiles}
            onSelectProfile={(profileId) => {
              setSelectedProfileId(profileId);
              setActiveTab('profiles');
            }}
            isSidebarOpen={isSidebarOpen}
            onToggleSidebar={toggleSidebar}
          />
        </div>

        <div className={`flex-1 flex overflow-hidden ${activeTab === 'brains' ? '' : 'hidden'}`}>
          <BrainPanel
            isSidebarOpen={isSidebarOpen}
            onToggleSidebar={toggleSidebar}
          />
        </div>

        <div className={`flex-1 flex overflow-hidden ${activeTab === 'profiles' ? '' : 'hidden'}`}>
          {/* Center: Interactive KasmVNC Viewer */}
          <VncViewer
            profile={selectedProfile}
            onAction={handleAction}
            isInspectorOpen={isInspectorOpen}
            onToggleInspector={toggleInspector}
            isSidebarOpen={isSidebarOpen}
            onToggleSidebar={toggleSidebar}
          />

          {/* Right: Inspector Details */}
          <ProfileDetails
            profile={selectedProfile}
            onDelete={handleDelete}
            onEdit={() => setIsEditModalOpen(true)}
            isOpen={isInspectorOpen}
            onToggle={toggleInspector}
            onRefresh={loadData}
          />
        </div>
      </div>

      {/* Persistent Bottom Telemetry */}
      <TelemetryFooter stats={systemStats} />

      {/* Creation Modal */}
      <CreateProfileModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onCreate={handleCreate}
        existingCount={profiles.length}
        proxies={proxies}
      />

      {/* Edit Profile Modal */}
      <EditProfileModal
        profile={selectedProfile}
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        onSave={handleUpdate}
        proxies={proxies}
      />

      {profileActionError && (
        <ProfileActionErrorModal
          {...profileActionError}
          onClose={() => setProfileActionError(null)}
        />
      )}
    </div>
  );
};

export default App;

export interface BrainVersion {
  id?: string;
  version?: string;
  directory: string;
  digest?: string;
  brain_api_version?: number;
  name?: string;
  description?: string;
  released_at?: string | null;
  change_log?: string;
  supported_locales?: string[];
  supported_themes?: string[];
  install_source?: 'bundled' | 'local_upload' | 'local' | string;
  installed_at?: string | null;
  archive_sha256?: string | null;
  status: 'valid' | 'invalid';
  error?: string;
}

export interface BrainFamily {
  id: string;
  active_version: string;
  previous_version?: string | null;
  channel: 'stable' | 'canary' | string;
  updated_at?: string | null;
  versions: BrainVersion[];
}

export interface BrainCatalogResponse {
  engine_version: string;
  brains: BrainFamily[];
}

export interface BrainActionResponse {
  success: boolean;
  active_version?: string;
  previous_version?: string | null;
  brain?: {
    id: string;
    version: string;
    digest: string;
    brain_api_version: number;
  };
}

export interface BrainUploadResponse {
  success: boolean;
  installed: {
    id: string;
    version: string;
    directory: string;
    digest: string;
    brain_api_version: number;
    install_source: string;
    archive_sha256: string;
  };
  activation?: BrainActionResponse | null;
}

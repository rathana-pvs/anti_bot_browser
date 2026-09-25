export interface AutomationLog {
  timestamp: string;
  profile_id?: string;
  level: 'STEP' | 'INFO' | 'WARN' | 'SUCCESS' | 'ERROR' | string;
  message: string;
}

export interface AutomationTaskState {
  profile_id: string;
  task?: 'warming' | 'post' | 'reel' | 'comment';
  status: 'idle' | 'running' | 'completed' | 'failed' | 'uncertain' | 'stopped';
  started_at?: string;
  ended_at?: string | null;
  logs: AutomationLog[];
  result?: any;
  error?: string | null;
  evidence_dir?: string | null;
  execution_id?: string;
  source?: 'queue' | string;
}

export interface RunAutomationParams {
  profile_id: string;
  task: 'warming' | 'post' | 'reel' | 'comment';
  scrolls?: number;
  caption?: string;
  comment_link?: string;
  media?: string;
}

export interface QueueExecutionItem {
  execution_id: string;
  batch_id?: string;
  batch_name?: string;
  post_id?: string;
  profile_id: string;
  post_type?: 'photo' | 'reel';
  media_file?: string;
  base_caption?: string;
  spun_caption: string;
  first_comment?: string | null;
  scheduled_at: string;
  status: 'pending' | 'running' | 'published' | 'failed' | 'failed_before_publish' | 'uncertain' | string;
  stage?: string | null;
  retry_count: number;
  error?: string | null;
  published_at?: string | null;
  logs?: string[];
  review_status?: 'resolved_published' | 'resolved_not_published' | 'needs_review' | string | null;
  review_note?: string | null;
  reviewed_at?: string | null;
  post_url?: string | null;
  post_url_verified_at?: string | null;
  post_match_confidence?: number | null;
  evidence_dir?: string | null;
}

export interface QueuePostItem {
  post_id: string;
  type: 'photo' | 'reel';
  media_file: string;
  base_caption: string;
  first_comment?: string | null;
  ai_spin?: boolean;
  executions: QueueExecutionItem[];
}

export interface DailyBatch {
  batch_id: string;
  name: string;
  created_at: string;
  target_profiles: string[];
  schedule_window: {
    start_time: string;
    end_time: string;
    profile_stagger_minutes: number;
  };
  posting_order_per_profile?: Record<string, number[]>;
  posts: QueuePostItem[];
}

export interface QueueDataResponse {
  queue_version: string;
  stats: {
    total: number;
    pending: number;
    running: number;
    published: number;
    failed: number;
    uncertain?: number;
    skipped: number;
  };
  batches: DailyBatch[];
  executions: QueueExecutionItem[];
}

export interface CreateBatchParams {
  name: string;
  target_profiles: string[];
  start_now?: boolean;
  schedule_window: {
    start_time: string;
    end_time: string;
    profile_stagger_minutes: number;
    start_now?: boolean;
  };
  posts: {
    type: 'photo' | 'reel';
    media_file: string;
    base_caption: string;
    first_comment?: string;
    ai_spin?: boolean;
  }[];
}

export interface MediaItem {
  filename: string;
  original_name?: string;
  size_bytes: number;
  created_at?: string;
  type: 'photo' | 'reel';
  url?: string;
}

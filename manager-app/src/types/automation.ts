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
  post_type?: 'photo' | 'reel' | 'warming';
  scrolls?: number;
  media_file?: string;
  base_caption?: string;
  spun_caption: string;
  first_comment?: string | null;
  scheduled_at: string;
  status: 'pending' | 'running' | 'published' | 'failed' | 'failed_before_publish' | 'uncertain' | string;
  stage?: string | null;
  stage_history?: Array<{
    stage: string;
    timestamp: string;
    reason?: string;
    interrupted_stage?: string;
  }>;
  stage_updated_at?: string | null;
  last_active_stage?: string | null;
  recovered_at?: string | null;
  preparation_mode?: 'off' | 'brief' | 'extended';
  preparation_status?: 'not_requested' | 'pending' | 'ready' | 'failed' | 'needs_review' | string;
  preparation_completed_at?: string | null;
  preparation_evidence_dir?: string | null;
  scheduler_lease?: {
    lease_id: string;
    owner_id?: string;
    kind: 'publisher' | 'preparer';
    profile_id: string;
    claimed_at?: string;
    heartbeat_at?: string;
    expires_at: string;
  } | null;
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
  permalink_status?: 'verified' | 'unresolved' | string | null;
  permalink_source?: string | null;
  permalink_note?: string | null;
  first_comment_status?: 'submitted_verified' | 'submitted_unverified' | 'submission_pending' | 'failed_input_not_found' | 'not_requested' | string | null;
  first_comment_method?: 'profile_first_post' | 'verified_permalink_fallback' | string | null;
  first_comment_verified_at?: string | null;
  first_comment_evidence_dir?: string | null;
  first_comment_source?: string | null;
  first_comment_note?: string | null;
  evidence_dir?: string | null;
  telemetry?: {
    schema_version: string;
    total_duration_ms: number;
    stage_durations_ms?: Record<string, number>;
    ocr?: {
      calls: number;
      inference_calls?: number;
      inference_total_duration_ms?: number;
      cache_hits?: number;
      cache_hit_rate_pct?: number | null;
      median_duration_ms?: number | null;
      p95_duration_ms?: number | null;
      samples?: Array<{
        duration_ms: number;
        region: string;
        candidate_count: number;
        max_confidence?: number | null;
        outcome?: string;
      }>;
    };
    locators?: {
      tier_counts?: Record<string, number>;
      fallback_count?: number;
    };
    semantic_fallbacks?: Array<{
      goal: string;
      state: string;
      provider?: string | null;
      model?: string | null;
      latency_ms?: number | null;
      confidence?: number | null;
      candidate_id?: string | null;
      shadow_mode?: boolean;
      validated?: boolean;
      validation_reason?: string | null;
      fresh_candidate_confirmed?: boolean;
      observed_state?: string | null;
    }>;
    environment?: {
      screen_size?: number[] | null;
      configured_screen_resolution?: string | null;
      locale?: string | null;
      theme?: string | null;
      theme_confidence?: number | null;
      browser_zoom?: number | null;
    };
  } | null;
}

export interface QueuePostItem {
  post_id: string;
  type: 'photo' | 'reel' | 'warming';
  media_file: string;
  base_caption: string;
  scrolls?: number;
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
    profile_stagger_seconds?: number;
    profile_stagger_minutes?: number;
    session_preparation_mode?: 'off' | 'brief' | 'extended';
    start_now?: boolean;
  };
  profile_execution_order?: string[];
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
    completed?: number;
    failed: number;
    uncertain?: number;
    skipped: number;
  };
  scheduler?: {
    config: {
      max_publishers: number;
      max_preparers: number;
      max_total_automation_tasks: number;
      max_active_profile_containers?: number;
      lease_ttl_ms: number;
      heartbeat_interval_ms: number;
    };
    active: { publishers: number; preparers: number; total: number };
    available: { publishers: number; preparers: number; total: number };
    leases: Array<{
      lease_id: string;
      owner_id?: string;
      kind: 'publisher' | 'preparer';
      profile_id: string;
      execution_id?: string;
      claimed_at?: string;
      heartbeat_at?: string;
      expires_at: string;
    }>;
  };
  telemetry_summary?: {
    terminal_executions: number;
    measured_executions: number;
    confirmed_publication_rate_pct: number | null;
    uncertain_rate_pct: number | null;
    failed_before_publish_rate_pct: number | null;
    human_review_rate_pct: number | null;
    median_execution_duration_ms: number | null;
    p95_execution_duration_ms: number | null;
    ocr_calls: number;
    ocr_lookups?: number;
    ocr_inference_calls?: number;
    ocr_cache_hits?: number;
    ocr_cache_hit_rate_pct?: number | null;
    median_ocr_duration_ms: number | null;
    p95_ocr_duration_ms: number | null;
    ocr_by_region: Record<string, {
      calls: number;
      inference_calls?: number;
      cache_hits?: number;
      cache_hit_rate_pct?: number | null;
      average_duration_ms: number;
      p95_duration_ms: number | null;
    }>;
    locator_tier_counts: Record<string, number>;
    locator_fallback_rate_pct: number | null;
    semantic_proposals?: number;
    semantic_validated?: number;
    semantic_validation_rate_pct?: number | null;
    semantic_fresh_confirmed?: number;
    semantic_shadow_blocked?: number;
    median_semantic_latency_ms?: number | null;
    p95_semantic_latency_ms?: number | null;
    semantic_by_state?: Record<string, {
      proposals: number;
      validated: number;
      fresh_confirmed: number;
    }>;
  };
  batches: DailyBatch[];
  executions: QueueExecutionItem[];
}

export type ResourceMode = 'auto' | 'low' | 'medium' | 'high';

export interface ResourceModeSettings {
  selected_mode: ResourceMode;
  effective_mode: Exclude<ResourceMode, 'auto'>;
  recommended_mode: Exclude<ResourceMode, 'auto'>;
  limits: {
    max_publishers: number;
    max_preparers: number;
    max_total_automation_tasks: number;
    max_active_profile_containers: number;
    ocr_threads_per_worker: number;
  };
  hardware: {
    total_memory_gb: number;
    cpu_threads: number;
    cpu_model: string;
    ocr: {
      device: 'cpu' | 'cuda';
      label: 'CPU' | 'NVIDIA GPU' | string;
      nvidia_detected: boolean;
      cuda_runtime_available: boolean;
      gpu_name: string | null;
      fallback_reason: string | null;
    };
  };
  supported_modes: Record<'low' | 'medium' | 'high', boolean>;
  runtime: {
    memory_used_percent: number;
    sustained_cpu_percent: number;
    active_profile_containers: number;
    admission_allowed: boolean;
    admission_reason: string;
    memory_paused: boolean;
  };
}

export interface CreateBatchParams {
  name: string;
  target_profiles: string[];
  start_now?: boolean;
  schedule_window: {
    start_time: string;
    end_time: string;
    profile_stagger_seconds: number;
    profile_stagger_minutes?: number;
    session_preparation_mode?: 'off' | 'brief' | 'extended';
    start_now?: boolean;
  };
  posts: {
    type: 'photo' | 'reel' | 'warming';
    media_file: string;
    base_caption: string;
    scrolls?: number;
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

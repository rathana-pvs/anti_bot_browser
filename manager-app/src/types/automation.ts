export interface WarmingOptions {
  random_scrolls: boolean;
  min_scrolls: number;
  max_scrolls: number;
  pace: 'quick' | 'balanced' | 'relaxed';
  surface: 'random' | 'news_feed' | 'profile';
  reread: boolean;
  long_breaks: boolean;
  cursor_movement: boolean;
  return_to_top: boolean;
  max_seconds: number;
  /** Set by auto warming: a varied read-only visit instead of one feed scroll. */
  auto?: boolean;
}

export interface AutomationLog {
  timestamp: string;
  profile_id?: string;
  level: 'STEP' | 'INFO' | 'WARN' | 'SUCCESS' | 'ERROR' | string;
  message: string;
}

export interface AutomationTaskState {
  profile_id: string;
  task?: 'warming' | 'post' | 'reel' | 'comment' | 'live';
  status: 'idle' | 'running' | 'completed' | 'failed' | 'uncertain' | 'needs_review' | 'stopped';
  started_at?: string;
  ended_at?: string | null;
  logs: AutomationLog[];
  result?: any;
  error?: string | null;
  evidence_dir?: string | null;
  comment_status?: 'attempted' | 'confirmed' | 'needs_review';
  comment_error_code?: string | null;
  end_after_comment?: boolean;
  completion_reason?: 'comment_verified' | null;
  execution_id?: string;
  source?: 'queue' | string;
}

export interface RunAutomationParams {
  profile_id: string;
  task: 'warming' | 'post' | 'reel' | 'comment';
  scrolls?: number;
  warming_options?: WarmingOptions;
  caption?: string;
  comment_link?: string;
  media?: string;
  brain_version?: string;
}

export interface QueueExecutionItem {
  comment_status?: 'attempted' | 'confirmed' | 'needs_review';
  comment_error_code?: string | null;
  end_after_comment?: boolean;
  completion_reason?: 'comment_verified' | null;
  network_recovery?: {
    state: string;
    started_at: string;
    deadline_at: string;
    next_check_at?: string | null;
    retry_count: number;
    check_count: number;
    last_checked_at?: string;
    last_measurement?: {
      upload?: { success: boolean; mbps?: number | null };
      download?: { success: boolean; mbps?: number | null };
      facebook?: { success: boolean };
      error?: string;
    };
  };
  elapsed_seconds?: number;
  duration_seconds?: number;
  broadcast_confirmed?: boolean;
  execution_id: string;
  batch_id?: string;
  batch_name?: string;
  post_id?: string;
  profile_id: string;
  post_type?: 'photo' | 'reel' | 'live' | 'warming';
  scrolls?: number;
  warming_options?: WarmingOptions;
  media_file?: string;
  title?: string;
  pinned_comment?: string | null;
  base_caption?: string;
  spun_caption: string;
  first_comment?: string | null;
  scheduled_at: string;
  /** Estimated start of a waiting item, from the same checks the scheduler uses. */
  expected_start_at?: string | null;
  wait_reason?: QueueWaitReason;
  account_timezone?: string | null;
  awake_hours?: [number, number] | null;
  status: 'pending' | 'running' | 'published' | 'failed' | 'failed_before_publish' | 'failed_after_publish' | 'uncertain' | string;
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
  warming_surface_requested?: 'news_feed' | 'profile' | null;
  warming_surface?: 'news_feed' | 'profile' | null;
  warming_surface_fallback?: boolean;
  warming_duration_seconds?: number | null;
  warming_scroll_actions?: number | null;
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
  /** How many log lines the item has; the queue list leaves the lines out. */
  log_count?: number;
  review_status?: 'resolved_published' | 'resolved_not_published' | 'needs_review' | string | null;
  review_note?: string | null;
  reviewed_at?: string | null;
  post_url?: string | null;
  post_url_verified_at?: string | null;
  post_match_confidence?: number | null;
  permalink_status?: 'verified' | 'captured' | 'recovered' | 'missing_after_recovery' | 'unresolved' | 'rejected_invalid' | string | null;
  permalink_recovery_attempted?: boolean;
  permalink_missing?: boolean;
  permalink_source?: string | null;
  permalink_note?: string | null;
  first_comment_status?: 'submitted_verified' | 'submitted_unverified' | 'submission_pending' | 'failed_input_not_found' | 'not_requested' | 'phone_pending' | 'phone_commenting' | string | null;
  first_comment_method?: 'profile_first_post' | 'verified_permalink_fallback' | string | null;
  first_comment_verified_at?: string | null;
  first_comment_evidence_dir?: string | null;
  first_comment_source?: string | null;
  first_comment_note?: string | null;
  first_comment_retry_count?: number;
  comment_retry_status?: 'running' | 'completed_verified' | 'failed_before_submission' | 'needs_review' | 'interrupted' | string | null;
  comment_retry_started_at?: string | null;
  comment_retry_ended_at?: string | null;
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
  type: 'photo' | 'reel' | 'live' | 'warming';
  media_file: string;
  title?: string;
  pinned_comment?: string | null;
  base_caption: string;
  scrolls?: number;
  warming_options?: WarmingOptions;
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
    batch_iteration_delay_seconds?: number;
    session_preparation_mode?: 'off' | 'brief' | 'extended';
    start_now?: boolean;
    start_at?: string;
    spread_minutes?: number;
    follow_active_hours?: boolean;
    timing?: 'now' | 'auto' | 'exact';
  };
  profile_execution_order?: string[];
  posting_order_per_profile?: Record<string, number[]>;
  posts: QueuePostItem[];
  /** Nothing new in the batch starts while it is paused. */
  paused?: boolean;
  paused_at?: string;
  edit_history?: { at: string; changes: string[]; jobs: number }[];
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
    batch_owner_id?: string | null;
    config: {
      max_publishers: number;
      max_post_publishers: number;
      max_live_publishers: number;
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

export type ResourceMode = 'auto' | 'extreme' | 'custom';
export type OcrEngine = 'paddleocr-v5' | 'paddleocr-v6' | 'easyocr';

export interface ResourceModeSettings {
  ocr_engine: {
    selected: OcrEngine;
    active: OcrEngine;
    restart_required: boolean;
    options: { value: OcrEngine; label: string }[];
  };
  selected_mode: ResourceMode;
  effective_mode: ResourceMode;
  recommended_mode: ResourceMode;
  limits: {
    max_publishers: number;
    max_post_publishers: number;
    max_live_publishers: number;
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
  supported_modes: Record<ResourceMode, boolean>;
  runtime: {
    memory_used_percent: number;
    sustained_cpu_percent: number;
    active_profile_containers: number;
    admission_allowed: boolean;
    admission_reason: string;
    memory_paused: boolean;
    ocr_worker?: {
      status: 'starting' | 'initializing' | 'ready' | 'error' | 'stopped';
      device: 'cpu' | 'cuda' | 'unavailable' | 'initializing' | null;
      cache_entries: number;
      error: string | null;
    };
  };
  adaptive?: {
    enabled: boolean;
    custom: { max_parallel: number; max_posts: number; max_live: number; reserve_percent: number };
    max_parallel: number | null;
    reserve_percent: number;
    decision: {
      state: 'calm' | 'busy' | 'pressure';
      limit: number;
      ceiling: number;
      start_spacing_ms: number;
      admit: boolean;
      hold: boolean;
      reason: string;
      predicted_memory_percent: number;
      container_memory_mb: number;
    };
  };
}

export interface AdaptiveLimitsUpdate {
  max_parallel?: number;
  max_posts?: number;
  max_live?: number;
  reserve_percent?: number;
}

export type QueueWaitReason = 'scheduled' | 'planned' | 'replanning' | 'active_hours' | 'active_hours_limit' | 'travelling' | 'on_hold'
  | 'waiting_for_visit' | 'previous_post' | 'rest_between_posts' | 'network_recovery' | 'waiting_for_slot' | 'starting' | 'prepared'
  | 'paused';

export interface CreateBatchParams {
  name: string;
  target_profiles: string[];
  start_now?: boolean;
  schedule_window: {
    start_time: string;
    end_time: string;
    profile_stagger_seconds: number;
    profile_stagger_minutes?: number;
    batch_iteration_delay_seconds?: number;
    session_preparation_mode?: 'off' | 'brief' | 'extended';
    start_now?: boolean;
    /** Full start date and time picked on this PC, ISO with offset. */
    start_at?: string;
    /** Each account starts at a random moment inside this many minutes (0: 45–120 s apart). */
    spread_minutes?: number;
    /** Off: start at the scheduled time even when it is night for the account. */
    follow_active_hours?: boolean;
    /** now: right away · auto: Follow active hours, planned per account · exact: start_at for all. */
    timing?: 'now' | 'auto' | 'exact';
    /** From the schedule preview, so the batch gets the plan the form showed. */
    plan_seed?: number;
  };
  posts: {
    read_more?: { preparation_id: string; captions?: Record<string, string> };
    playback_mode?: 'auto' | 'manual';
    muted?: boolean;
    loop?: boolean;
    max_duration_seconds?: number;
    type: 'photo' | 'reel' | 'live' | 'warming';
    media_file: string;
    title?: string;
    pinned_comment?: string | null;
    base_caption: string;
    scrolls?: number;
  warming_options?: WarmingOptions;
    first_comment?: string;
    ai_spin?: boolean;
  }[];
}

export interface AppendBatchPostInput {
  type: 'photo' | 'reel';
  media_file: string;
  base_caption: string;
  first_comment?: string;
  ai_spin?: boolean;
}

export interface MediaItem {
  filename: string;
  original_name?: string;
  size_bytes: number;
  created_at?: string;
  type: 'photo' | 'reel';
  url?: string;
}

export interface AutoWarmingSummary {
  enabled: boolean;
  visits_today: number;
  /** Accounts on a visit right now. */
  running: string[];
  last_choice: { profile_id: string; reason: string; at: string } | null;
}

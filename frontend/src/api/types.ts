// Shapes returned by the backend (see backend/src/twin/schemas.py and the route modules).
// Only fields the dashboard uses are typed; everything comes from the live API.

export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type IncidentStatus = 'OPEN' | 'INVESTIGATING' | 'RESOLVED' | 'FALSE_POSITIVE';

export interface Vertex { vertex_order: number; x: number; y: number }

export interface Zone {
  zone_id: string;
  name: string;
  zone_type: string;
  security_level: number;
  max_occupancy: number | null;
  current_occupancy: number;
  min_x: number; min_y: number; max_x: number; max_y: number;
  vertices: Vertex[];
}

export interface Track {
  track_id: string;
  person_id: string | null;
  current_zone_id: string | null;
  current_camera_id: string | null;
  position: [number, number] | null;
  first_seen: string;
  last_seen: string;
  dwell_seconds: number;
  confidence: number;
}

export interface TwinState {
  timestamp: string;
  total_active_tracks: number;
  zone_occupancy: Record<string, number>;
  active_tracks: Track[];
}

export interface Incident {
  incident_id: string;
  incident_type: string;
  severity: Severity;
  status: IncidentStatus;
  zone_id: string | null;
  track_id: string | null;
  person_id: string | null;
  start_time: string;
  end_time: string | null;
  supporting_event_ids: string[];
  summary: string;
}

export interface SiteCamera {
  camera_id: string;
  name: string;
  zone_id: string | null;
  positioning: 'metric' | 'room' | null;
  in_live_config: boolean;
  feeds_twin: boolean | null;
}

export interface SiteDoor {
  door_id: string;
  name: string;
  zone_id: string;
  camera_id: string;
  badge_required: boolean;
  jambs_m: [number, number][] | null;
}

export interface Site { name?: string; cameras: SiteCamera[]; doors: SiteDoor[]; notes: string[]; live_config: string | null }

export interface LatencyStat { p50: number | null; p95: number | null }

export interface LiveCamera {
  camera_id: string;
  state: string;              // loading_model | running | error | stopped
  feed_twin: boolean;
  detect: boolean;
  device: string | null;
  target_fps: number;
  processed_fps: number;
  frames_processed: number;
  missed_slots: number;
  people_in_view: number;
  incidents_raised: number;
  latency_ms: Record<string, LatencyStat>;
  last_error: string | null;
  source: {
    state: string;            // connecting | live | reconnecting | ended | stopped
    url: string;
    frames_read: number;
    source_fps: number;
    reconnects: number;
    size: [number, number] | null;
    last_error: string | null;
  };
}

export interface LiveStatus {
  running: boolean;
  config_path: string | null;
  started_at: string | null;
  process_fps: number | null;
  cameras: LiveCamera[];
  totals?: { cameras: number; live: number; people_in_view: number; processed_fps: number; missed_slots: number };
  evidence?: { clips_written: number; recording: Record<string, { incidents: number; frames: number }>; last_error: string | null } | null;
}

export interface TwinEvent {
  sequence_num: number;
  event_id: string;
  event_type: string;
  timestamp: string;
  track_id: string;
  person_id: string | null;
  camera_id: string | null;
  zone_id: string | null;
  previous_zone_id: string | null;
  confidence: number | null;
  coordinates: { x: number; y: number } | null;
  payload: Record<string, unknown>;
}

export interface EvidenceClip {
  camera_id: string;
  video: string;
  key_frame: string;
  video_url: string;
  key_frame_url: string;
  clip_start: string;
  clip_end: string;
  frames: number;
  incident_offset_s: number;
  track: string | null;
}

export interface EvidenceManifest { incident_id: string; clips: EvidenceClip[] }

export interface ClaimCheck {
  claim_id: string;
  kind: string;
  text: string;
  event_ids: string[];
  verdict: string;            // supported | partially_supported | unsupported | unchecked
  judge_reason?: string;
  deterministic_issues?: string[];
}

export interface Verification {
  passed?: boolean;
  json_valid?: boolean;
  ids_passed?: boolean;
  unverified_ids?: string[];
  banned_words?: string[];
  missing_keys?: string[];
  truncated?: boolean;
  claims?: {
    claims?: ClaimCheck[];
    unsupported?: number;
    partially_supported?: number;
    unchecked?: number;
    passed?: boolean;
  };
}

export interface InvestigationResult {
  summary?: string;
  timeline?: { time: string; description: string; event_ids: string[] }[];
  findings?: { statement: string; kind: 'observation' | 'inference' | string; event_ids: string[] }[];
  person_identity?: string;
  benign_explanations?: string[];
  missing_evidence?: string[];
  recommended_actions?: string[];
  confidence?: string;
  answer?: string;
}

export interface Investigation {
  investigation_id: string;
  incident_id: string;
  created_at: string;
  status: string;             // COMPLETED | INVALID_OUTPUT | FAILED ...
  model_id: string;
  question: string | null;
  result: InvestigationResult | null;
  verification: Verification;
  cost_usd: number;
  latency_ms: number;
  error: string | null;
}

export interface InvestigationList {
  incident_id: string;
  investigations: Investigation[];
  spent_today_usd: number;
  daily_budget_usd: number;
}

export interface AutoStatus {
  enabled: boolean;
  min_severity?: string;
  cooldown_seconds?: number;
  max_per_hour?: number;
  paused_seconds_left?: number;
  counts?: Record<string, number>;
  recent?: { incident_id: string; incident_type: string; zone_id: string | null; outcome: string; at: string; detail?: string }[];
}

export interface Scenario { name: string; description: string; step_count: number }

export interface Heatmap { width_m: number; height_m: number; cell_size_m: number; cols: number; rows: number; cells: number[][] }

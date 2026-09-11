// 知寻结果类型（与 pipeline 6_report.json 对齐）
export interface Answer {
  content_id: string;
  question_title: string;
  text: string;
  votes: number;
  comments: number;
  author: string;
  edit_time: number;
  url: string;
  claim_count: number;
  unique_claims: number;
  anchored_claims: number;
  detailed_claims?: number;
  sensitive_claims: number;
  sensitive_unverified_claims: number;
  rarity_weighted_v: number;
  E_answer: number;
  freshness: number;
  info_score: number;
  age_days: number | null;
  expo_correction: number;
  L_inf: number;
  in_observation_pool: boolean;
  underestimate_index: number | null;
  badges: string[];
  dominant_cluster: number;
  position: { x: number; y: number };
  excavation_reason: string;
  claims?: AnswerClaim[];
  votes_unknown?: boolean;
}

export interface ClusterClaim {
  claim: string;
  author: string;
  content_id: string;
  url: string;
}

export interface AnswerClaim {
  claim: string;
  type: string;
  verifiable: boolean;
  unique?: boolean;
  has_detail?: boolean;
}

export interface ClusterView {
  cluster: number;
  label: string;
  summary: string;
  claim_count: number;
  answer_count: number;
  representative_answer: string | null;
  claims?: ClusterClaim[];
  claims_truncated?: boolean;
}

export interface Monopoly {
  top_k: number;
  exposure_share_top_k: number;
  info_increment_share_top_k: number;
  monopoly_gap: number;
  total_sample_answers: number;
  question_total_answers: number | null;
}

export interface Result {
  title: string;
  question_url: string | null;
  sample_size: number;
  claim_total: number;
  cluster_count: number;
  noise_claim_count: number;
  noise_claims?: ClusterClaim[];
  clusters: ClusterView[];
  answers: Answer[];
  top_undervalued: string[];
  minority_report: string[];
  monopoly: Monopoly;
  metrics_note: string;
}

export interface TopicInfo {
  key: string;
  title: string;
  sample_size: number;
  pearl_count?: number;
}

export interface StageEvent {
  stage: string;
  name: string;
  done: number;
  total: number;
}

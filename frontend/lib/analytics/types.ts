export type AnalyticsEventName =
  | "page.view"
  | "page.leave"
  | "cta.click"
  | "import.start"
  | "import.success"
  | "import.failed"
  | "opening.view"
  | "game.view"
  | "analysis.light.start"
  | "analysis.light.success"
  | "analysis.deep.requested"
  | "analysis.deep.blocked_signup"
  | "analysis.deep.started"
  | "analysis.deep.completed"
  | "analysis.deep.failed"
  | "analysis.ai.requested"
  | "analysis.ai.completed"
  | "analysis.ai.failed"
  | "analysis.ai.blocked_signup"
  | "insights.refresh.requested"
  | "insights.refresh.completed"
  | "auth.signin.clicked"
  | "auth.registered"
  | "identity.linked"
  | "feature.usage";

export interface AnalyticsEventPayload {
  event_id: string;
  event_name: string;
  event_version: string;
  occurred_at: string;
  anonymous_id: string;
  session_id: string;
  path?: string;
  url?: string;
  referrer?: string;
  user_agent?: string;
  is_first_time?: boolean;
  properties: Record<string, unknown>;
}

export interface TrackEventOptions {
  properties?: Record<string, unknown>;
  path?: string;
  url?: string;
  referrer?: string;
  eventVersion?: string;
  isFirstTime?: boolean;
}

export interface AnalyticsIngestBody {
  events: AnalyticsEventPayload[];
}

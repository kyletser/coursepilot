export type UserRole = "STUDENT" | "TEACHER";

export type User = {
  id: string;
  email: string;
  role: UserRole;
  status: string;
  created_at: string;
};

export type AuthTokens = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  refresh_expires_in: number;
};

export type Course = {
  id: string;
  owner_id: string;
  template: "DATA_STRUCTURES" | "OPERATING_SYSTEMS" | string;
  code: string;
  name: string;
  description: string;
  semester: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type CreatedCourse = Course & {
  invite_code: string;
  invite_expires_at: string;
};

export type Invite = {
  course_id: string;
  invite_code: string;
  expires_at: string;
};

export type Enrollment = {
  id: string;
  course_id: string;
  student_id: string;
  status: string;
  joined_at: string;
};

export type JoinedCourse = {
  enrollment: Enrollment;
  course: Course;
};

export type EnrolledStudent = {
  id: string;
  email: string;
  status: string;
  enrollment_id: string;
  enrollment_status: string;
  joined_at: string;
};

export type DocumentRecord = {
  id: string;
  logical_name?: string;
  name?: string;
  original_filename?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  latest_version?: {
    id?: string;
    version?: number | string;
    status?: string;
    ingestion_job_id?: string;
  };
  ingestion_job?: {
    id?: string;
    stage?: string;
    progress?: number;
    error_code?: string | null;
  };
};

export type GraphCandidate = {
  id: string;
  name?: string;
  description?: string;
  type?: string;
  relation_type?: string;
  from_name?: string;
  to_name?: string;
  confidence?: number;
  status?: string;
  evidence?: string;
  source_excerpt?: string;
  source_chunk_id?: string;
};

export type GraphCandidates = {
  concepts: GraphCandidate[];
  relations: GraphCandidate[];
};

export type QuizItem = {
  id: string;
  question: string;
  options: string[] | Record<string, string>;
  difficulty?: string;
  status?: string;
  concept_id?: string;
  concept_name?: string;
  explanation?: string;
  source_excerpt?: string;
};

export type QuizAttempt = {
  id?: string;
  correct?: boolean;
  answer?: string;
  selected_answer?: string;
  explanation?: string;
  mastery?: number;
};

export type MasteryState = {
  id?: string;
  concept_id: string;
  concept_name?: string;
  name?: string;
  mastery: number;
  attempt_count?: number;
  last_assessed_at?: string | null;
};

export type LearningPathStep = {
  concept_id?: string;
  concept_name?: string;
  name?: string;
  title?: string;
  reason?: string;
  recommended_reason?: string;
  mastery?: number;
  materials?: Array<{ id?: string; title?: string; name?: string }>;
};

export type LearningPath = {
  target_concept_id?: string;
  target_concept_name?: string;
  steps: LearningPathStep[];
};

export type Citation = {
  citation_id?: string;
  label?: number;
  document?: string;
  page?: number | null;
  section?: string;
  quote?: string;
  chunk_id?: string;
};

export type ChatStreamEvent =
  | { type: "status"; data: { stage?: string; trace_id?: string } }
  | {
      type: "retrieval";
      data: {
        query?: string;
        candidate_count?: number;
        index_version?: string;
      };
    }
  | { type: "token"; data: { text?: string } }
  | { type: "citation"; data: Citation }
  | {
      type: "done";
      data: { message_id?: string; intent?: string; usage?: unknown };
    }
  | {
      type: "error";
      data: { code?: string; message?: string; request_id?: string };
    };

export type ApiEnvelope<T> = {
  data: T | null;
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  } | null;
  request_id: string;
};

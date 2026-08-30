export type AnswerType = 'written' | 'select' | 'multi_select' | 'numeric' | 'grid_select' | 'practical';

export interface AnswerOption {
  key: string;
  text: string;
}

export interface GridRow {
  statement: string;
  options: string[];
}

export interface Question {
  id: string;
  paper_id: string;
  question_number: string;
  mark_value: number;
  question_text: string;
  marking_type: string;
  spec_code?: string;
  topic_title?: string;
  exam_board?: string;
  subject?: string;
  marking_dsl?: string;
  mark_scheme_text?: string;
  images?: any[];
  answer_type?: AnswerType;
  answer_options?: AnswerOption[] | GridRow[] | { unit: string | null } | null;
  previous_answer?: PreviousAnswer | null;
}

export interface PreviousAnswer {
  answer_text: string | null;
  answer_image_url: string | null;
  marks_awarded: number;
  marks_possible: number;
  feedback_text: string | null;
  missed_points: string[] | string | null;
  misconception_tags: string[] | string | null;
  marked_by: string;
  created_at: string | null;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  exam_board?: string;
  year_group?: string;
  is_admin?: boolean;
}

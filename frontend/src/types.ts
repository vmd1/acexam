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
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  exam_board?: string;
  year_group?: string;
}

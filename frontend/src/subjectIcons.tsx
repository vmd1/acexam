import {
  Leaf,
  FlaskConical,
  Zap,
  Sigma,
  Code2,
  Globe2,
  Landmark,
  Briefcase,
  BookOpen,
  Users,
  Brain,
  UtensilsCrossed,
  Wrench,
  Dumbbell,
  Film,
  Scale,
  CircleDollarSign,
  Image as ImageIcon,
  BookMarked,
  type LucideIcon,
} from 'lucide-react';

// Best-effort icon per subject name so the subject picker reads like a real
// icon set rather than a plain text list. Falls back to a generic book icon
// for anything not explicitly mapped (new subjects, unusual combined titles).
const ICON_BY_KEYWORD: [RegExp, LucideIcon][] = [
  [/biology/i, Leaf],
  [/chemistry/i, FlaskConical],
  [/physics/i, Zap],
  [/(^|\s)maths?($|\s)|mathematics/i, Sigma],
  [/computer science/i, Code2],
  [/geography/i, Globe2],
  [/history/i, Landmark],
  [/business/i, Briefcase],
  [/english/i, BookOpen],
  [/sociology/i, Users],
  [/psychology/i, Brain],
  [/food|nutrition/i, UtensilsCrossed],
  [/design and technology|engineering/i, Wrench],
  [/physical education/i, Dumbbell],
  [/film/i, Film],
  [/religious studies|citizenship/i, Scale],
  [/economics/i, CircleDollarSign],
  [/media studies/i, ImageIcon],
];

export function iconForSubject(subject: string): LucideIcon {
  const match = ICON_BY_KEYWORD.find(([re]) => re.test(subject));
  return match ? match[1] : BookMarked;
}

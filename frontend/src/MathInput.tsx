import { useEffect, useRef } from 'react';
import 'mathlive';
import 'mathlive/fonts.css';
import type { MathfieldElement } from 'mathlive';
import { latexToPlain } from './mathExpr';

type MathFieldProps = React.DetailedHTMLProps<React.HTMLAttributes<MathfieldElement>, MathfieldElement> & {
  placeholder?: string;
};

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'math-field': MathFieldProps;
    }
  }
}

interface MathInputProps {
  value: string;
  onChange: (plain: string) => void;
  placeholder?: string;
}

// Visual math editor (fraction bars, superscript boxes, sqrt) for entering
// simple numbers, fractions, indices, and algebraic expressions like
// quadratics or algebraic fractions. Emits a plain-text expression (via
// latexToPlain) that the backend's sympy-based marker can compare.
export default function MathInput({ value, onChange, placeholder }: MathInputProps) {
  const ref = useRef<MathfieldElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.mathVirtualKeyboardPolicy = 'auto';
    el.smartFence = true;
    el.smartSuperscript = true;

    const handleInput = () => {
      const latex = el.getValue('latex');
      onChange(latexToPlain(latex));
    };
    el.addEventListener('input', handleInput);
    return () => el.removeEventListener('input', handleInput);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Only push external value changes in (e.g. switching questions) -
    // avoid clobbering the caret position while the user is typing.
    if (el.getValue('latex') !== value && document.activeElement !== el) {
      el.setValue(value);
    }
  }, [value]);

  return (
    <math-field
      ref={ref}
      style={{
        fontSize: '1.15rem',
        padding: '0.5rem 0.9rem',
        borderRadius: '8px',
        border: '1px solid #cbd5e1',
        minWidth: '220px',
        display: 'inline-block',
      }}
      placeholder={placeholder ? `\\text{${placeholder}}` : undefined}
    />
  );
}

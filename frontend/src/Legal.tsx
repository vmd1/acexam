import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

// Static legal pages, reachable logged-in or logged-out (see App.tsx routes
// and the footer links in Landing.tsx / NavDrawer.tsx). Content is plain
// informational copy, not a substitute for legal advice - update it if the
// underlying product behavior (data collected, cookies, etc.) changes.

function LegalPage({ title, updated, children }: { title: string; updated: string; children: ReactNode }) {
  return (
    <div className="landing-features" style={{ display: 'block', maxWidth: 760, margin: '0 auto', padding: '2rem 1.5rem 4rem' }}>
      <h1 style={{ marginBottom: '0.25rem' }}>{title}</h1>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: 0 }}>Last updated: {updated}</p>
      <div className="legal-body">{children}</div>
    </div>
  );
}

export function TermsPage() {
  return (
    <LegalPage title="Terms & Conditions" updated="1 September 2026">
      <p>
        These terms govern your use of Acexam ("we", "us", "our"), a free revision platform that lets
        students practice UK exam-board past-paper questions and receive AI-generated marking and feedback.
        By creating an account or using the site, you agree to these terms.
      </p>

      <h2>1. The service</h2>
      <p>
        Acexam is provided free of charge, for personal, non-commercial revision use. We do not guarantee
        uninterrupted availability, and features, content, and question banks may change or be removed at
        any time.
      </p>

      <h2>2. Accounts</h2>
      <p>
        You must provide accurate registration information and keep your login credentials confidential.
        You're responsible for activity that happens under your account. Tell us if you believe your account
        has been accessed without authorization.
      </p>

      <h2>3. Acceptable use</h2>
      <p>You agree not to:</p>
      <ul>
        <li>Use the service for any unlawful purpose or to submit content that is abusive, infringing, or harmful;</li>
        <li>Attempt to disrupt, reverse-engineer, or gain unauthorized access to the platform or other users' data;</li>
        <li>Scrape, resell, or redistribute past-paper content, mark schemes, or AI-generated feedback in bulk;</li>
        <li>Impersonate another person or misrepresent your affiliation with any exam board.</li>
      </ul>

      <h2>4. Past-paper and mark-scheme content</h2>
      <p>
        Past-paper questions and mark schemes are sourced from publicly available exam board materials
        (e.g. AQA, Edexcel, OCR) for educational, non-commercial use. Copyright in original past-paper
        material remains with the relevant exam board. If you are a rights holder and have a concern about
        how material is used, please contact us and we will address it.
      </p>

      <h2>5. AI-generated marking and feedback</h2>
      <p>
        Marks, feedback, and mastery/analytics data are generated automatically (via a marking DSL and/or
        AI models) and are provided for revision purposes only. They are indicative, may contain errors, and
        are not an official prediction of grades or exam-board marking outcomes. Do not rely on them as a
        substitute for teacher or exam-board guidance.
      </p>

      <h2>6. Intellectual property</h2>
      <p>
        The Acexam name, branding, and platform (excluding third-party past-paper content) belong to us or
        our licensors. You may not copy or reuse it without permission.
      </p>

      <h2>7. Termination</h2>
      <p>
        We may suspend or terminate accounts that breach these terms or misuse the service. You may stop
        using the service, or request account deletion, at any time.
      </p>

      <h2>8. Liability</h2>
      <p>
        The service is provided "as is" without warranties of any kind. To the extent permitted by law, we
        are not liable for exam outcomes, data loss, or indirect losses arising from your use of the service.
      </p>

      <h2>9. Changes to these terms</h2>
      <p>
        We may update these terms from time to time. Continued use of the service after changes take effect
        means you accept the updated terms.
      </p>

      <h2>10. Contact</h2>
      <p>Questions about these terms? Contact us via the details in your account settings.</p>

      <p style={{ marginTop: '2rem' }}>
        See also our <Link to="/privacy">Privacy Policy</Link>.
      </p>
    </LegalPage>
  );
}

export function PrivacyPage() {
  return (
    <LegalPage title="Privacy Policy" updated="1 September 2026">
      <p>
        This policy explains what personal data Acexam collects, why, and how it's used, in line with UK
        GDPR and the Data Protection Act 2018.
      </p>

      <h2>1. Data we collect</h2>
      <ul>
        <li><strong>Account data:</strong> name/email, password (stored as a salted hash, never in plain text), and your selected subjects/exam boards/level.</li>
        <li><strong>Practice data:</strong> your answers to practice questions, submitted attempts, marks awarded, and any freehand/canvas drawings you submit as answers.</li>
        <li><strong>Derived analytics:</strong> topic and command-word mastery scores, misconception tags, and progress history, calculated from your practice data.</li>
        <li><strong>Technical data:</strong> basic request/session data (e.g. login tokens stored in a secure cookie) needed to keep you signed in and to rate-limit abuse.</li>
      </ul>

      <h2>2. How we use it</h2>
      <ul>
        <li>To run the core service: showing you relevant questions, marking your answers, and tracking your progress;</li>
        <li>To personalize practice (e.g. adaptive queues targeting weak topics or misconceptions);</li>
        <li>To maintain the security and integrity of the platform (authentication, rate limiting, abuse prevention);</li>
        <li>To improve the platform, including reviewing aggregated or anonymized usage patterns.</li>
      </ul>

      <h2>3. AI processing</h2>
      <p>
        Some of your submitted answers may be sent to third-party AI providers (e.g. Google Gemini or
        OpenAI-compatible services) to generate marking and feedback. These providers process the data
        solely to return a marking result and are not used to build a profile of you outside the service.
      </p>

      <h2>4. Legal basis</h2>
      <p>
        We process your data on the basis of performing our contract with you (providing the service you
        signed up for) and our legitimate interest in keeping the platform secure and improving it.
      </p>

      <h2>5. Data sharing</h2>
      <p>
        We do not sell your personal data. We share data only with service providers necessary to run
        Acexam (e.g. hosting, database, and AI-marking providers), under terms that require them to protect
        it, or where required by law.
      </p>

      <h2>6. Data retention</h2>
      <p>
        We retain account and practice data for as long as your account is active, so your progress history
        remains useful. You can request deletion of your account and associated data at any time.
      </p>

      <h2>7. Your rights</h2>
      <p>Under UK GDPR, you have the right to:</p>
      <ul>
        <li>Access the personal data we hold about you;</li>
        <li>Request correction of inaccurate data;</li>
        <li>Request deletion of your data ("right to be forgotten");</li>
        <li>Object to or restrict certain processing;</li>
        <li>Request a copy of your data in a portable format.</li>
      </ul>
      <p>To exercise any of these rights, contact us via the details in your account settings.</p>

      <h2>8. Cookies</h2>
      <p>
        We use a single essential cookie to keep you signed in (a secure, httpOnly authentication token).
        We do not use third-party advertising or tracking cookies.
      </p>

      <h2>9. Children's data</h2>
      <p>
        Acexam is designed for GCSE/A-Level students. If you are under 13, please have a parent or guardian
        review this policy and assist with account setup.
      </p>

      <h2>10. Changes to this policy</h2>
      <p>We may update this policy from time to time; the "last updated" date above will reflect the latest revision.</p>

      <p style={{ marginTop: '2rem' }}>
        See also our <Link to="/terms">Terms & Conditions</Link>.
      </p>
    </LegalPage>
  );
}

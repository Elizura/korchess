export default function PrivacyPage() {
  return (
    <main className="max-w-3xl mx-auto px-6 py-12 text-[color:var(--zen-text)]">
      <h1 className="text-3xl font-semibold mb-6">Privacy & Product Analytics</h1>
      <div className="space-y-4 text-sm leading-7 text-[color:var(--zen-muted)]">
        <p>
          Korchess uses product analytics to understand usage patterns, improve features, and measure
          conversion and retention.
        </p>
        <p>
          We collect pseudonymous analytics identifiers, session metadata, page and feature events, and
          approximate location (country/city) derived from IP. We do not store full IP addresses in
          analytics records.
        </p>
        <p>
          We do not collect raw game PGN, auth tokens, or passwords in analytics event payloads.
        </p>
        <p>
          Analytics data is used for internal product and business intelligence purposes, including
          funnels, activation, retention, and feature usage analysis.
        </p>
      </div>
    </main>
  );
}

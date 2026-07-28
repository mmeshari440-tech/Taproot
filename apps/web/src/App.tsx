import { Button } from "@/components/ui/button";

const SPRINTS = [
  { name: "0 · Foundations", state: "in progress" },
  { name: "1 · Auth & Admin", state: "todo" },
  { name: "2 · Pipeline", state: "todo" },
  { name: "3 · Agent", state: "todo" },
  { name: "4 · Results & Hardening", state: "todo" },
];

export default function App() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-8 px-6 py-16">
      <header className="space-y-2">
        <h1 className="text-4xl font-bold tracking-tight text-primary">Taproot</h1>
        <p className="text-muted-foreground">
          On-prem AI root-cause investigation across Elasticsearch, Sentry, and AppDynamics.
        </p>
      </header>

      <section aria-label="Build progress" className="space-y-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Sprints
        </h2>
        <ul className="divide-y divide-border rounded-lg border border-border">
          {SPRINTS.map((s) => (
            <li key={s.name} className="flex items-center justify-between px-4 py-3">
              <span>{s.name}</span>
              <span className="text-xs uppercase text-muted-foreground">{s.state}</span>
            </li>
          ))}
        </ul>
      </section>

      <Button className="self-start">Placeholder — auth arrives in T-08</Button>
    </main>
  );
}

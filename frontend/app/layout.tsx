import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Development Analytics",
  description:
    "Observabilidade do desenvolvimento de software assistido por IA.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pt-BR">
      {/*
        Browser extensions (ColorZilla, Grammarly, password managers) inject
        attributes into <body> before React hydrates, which React reports as a
        mismatch. suppressHydrationWarning applies to this element's own
        attributes and text only - never to its descendants - so a genuine
        mismatch inside the page is still reported.
      */}
      <body suppressHydrationWarning>
        <header className="topbar">
          <div className="topbar-inner">
            <div className="brand">
              AI Development Analytics
              <span>dados observados · Claude Code + ccusage + Git</span>
            </div>
            <nav className="nav">
              <a href="/">Visão geral</a>
              <a href="/projects">Projetos</a>
              <a href="/sessions">Sessões</a>
            </nav>
          </div>
        </header>
        <main className="shell">{children}</main>
      </body>
    </html>
  );
}

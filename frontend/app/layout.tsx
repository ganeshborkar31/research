import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "Knowledge AI Support Console",
  description: "Streaming support chat UI for Knowledge AI",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}

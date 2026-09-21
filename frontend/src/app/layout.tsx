import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Claus",
  description: "People and AI working in shared context",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

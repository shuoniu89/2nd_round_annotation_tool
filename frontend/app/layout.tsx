import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Annotation verification",
  description: "An independent annotation and AI verification workspace for research raters.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}


import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  distDir: process.env.CODECHECK_TEST_DIST_DIR || ".next",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.DJANGO_API_URL || "http://127.0.0.1:8000"}/api/:path*` }];
  },
};
export default config;


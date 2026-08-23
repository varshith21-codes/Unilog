import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emit a minimal self-contained Node server for the production container. Public
  // NEXT_PUBLIC_* values are still injected at build time, as required by Next.js.
  output: "standalone",
  experimental: {
    // TypeScript 7 ships the native compiler and no longer exposes the JS compiler API
    // Next.js uses for inline type checking. Driving the CLI instead keeps type errors
    // failing the build.
    useTypeScriptCli: true,
  },
};

export default nextConfig;

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    // TypeScript 7 ships the native compiler and no longer exposes the JS compiler API
    // Next.js uses for inline type checking. Driving the CLI instead keeps type errors
    // failing the build.
    useTypeScriptCli: true,
  },
};

export default nextConfig;

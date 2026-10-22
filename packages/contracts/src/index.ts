/**
 * Public API contract. Types are generated from docs/api/openapi.json (`pnpm gen:contracts`),
 * which is itself generated from the FastAPI models. Never edit schema.d.ts by hand.
 */
import type { components } from "./schema";

type S = components["schemas"];

export type { components, paths } from "./schema";
export type ErrorBody = S["ErrorBody"];
export type Health = S["Health"];
export type FormatOut = S["FormatOut"];
export type OptionOut = S["OptionOut"];
export type CapabilityOut = S["CapabilityOut"];
export type CapabilitiesOut = S["CapabilitiesOut"];
export type EngineOut = S["EngineOut"];
export type DetectionOut = S["DetectionOut"];
export type FileOut = S["FileOut"];
export type TargetOut = S["TargetOut"];
export type TargetsOut = S["TargetsOut"];
export type PlanRequest = S["PlanRequest"];
export type PlanOut = S["PlanOut"];
export type PlanStepOut = S["PlanStepOut"];
export type ConversionRequest = S["ConversionRequest"];
export type JobOut = S["JobOut"];
export type JobErrorOut = S["JobErrorOut"];
export type JobResultOut = S["JobResultOut"];
export type VerificationOut = S["VerificationOut"];
export type BatchItem = S["BatchItem"];
export type BatchRequest = S["BatchRequest"];
export type BatchOut = S["BatchOut"];
export type BatchResultOut = S["BatchResultOut"];
export type SignedLink = S["SignedLink"];

export type JobState = JobOut["state"];
export type ExecutionPolicy = PlanRequest["execution_policy"];
export type Fidelity = PlanOut["fidelity"];

export const TERMINAL_STATES: readonly JobState[] = ["SUCCEEDED", "FAILED", "CANCELLED"];
export const isTerminal = (s: JobState): boolean => (TERMINAL_STATES as readonly string[]).includes(s);

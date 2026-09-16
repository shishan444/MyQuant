import { api } from "./api";
import type { ValidateRequest, ValidateResponse, RuleValidateRequest, RuleValidateResponse } from "@/types/api";

export async function validateHypothesis(
  payload: ValidateRequest,
): Promise<ValidateResponse> {
    return api.post("/api/validate", { json: payload });
}

export async function validateRules(
  payload: RuleValidateRequest,
): Promise<RuleValidateResponse> {
    return api.post("/api/validate/rules", { json: payload });
}

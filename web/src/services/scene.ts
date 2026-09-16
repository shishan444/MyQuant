/** Scene verification API service. */
import { api } from "./api";
import type {
  SceneTypeInfo,
  SceneVerifyRequest,
  SceneVerifyResponse,
} from "@/types/scene";

export async function getSceneTypes(): Promise<{ types: SceneTypeInfo[] }> {
    return api.get("/api/scene/types");
}

export async function verifyScene(
  payload: SceneVerifyRequest,
): Promise<SceneVerifyResponse> {
    return api.post("/api/validate/scene", { json: payload });
}

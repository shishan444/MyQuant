/** 场景验证的 React Query hooks——场景模式面板消费。 */
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { verifyScene } from "@/services/scene";
import type { SceneVerifyRequest, SceneVerifyResponse } from "@/types/scene";

export function useVerifyScene() {
  return useMutation<SceneVerifyResponse, Error, SceneVerifyRequest>({
    mutationFn: verifyScene,
    onSuccess: (result) => {
      if (result.total_triggers === 0) {
        toast.info("未检测到场景触发点，请尝试调整参数或时间范围");
      }
    },
    onError: (err) => {
      toast.error(`场景验证失败: ${err.message}`);
    },
  });
}

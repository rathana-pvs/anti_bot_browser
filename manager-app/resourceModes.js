export const RESOURCE_MODE_LIMITS = Object.freeze({
  low: Object.freeze({
    max_publishers: 1,
    max_preparers: 1,
    max_total_automation_tasks: 2,
    max_active_profile_containers: 2,
  }),
  medium: Object.freeze({
    max_publishers: 2,
    max_preparers: 2,
    max_total_automation_tasks: 4,
    max_active_profile_containers: 4,
  }),
  high: Object.freeze({
    max_publishers: 3,
    max_preparers: 3,
    max_total_automation_tasks: 6,
    max_active_profile_containers: 6,
  }),
});

const MODE_RANK = Object.freeze({ low: 0, medium: 1, high: 2 });
const RANK_MODE = Object.freeze(['low', 'medium', 'high']);

export function recommendedResourceMode(totalMemoryGb, cpuThreads) {
  const memoryRank = totalMemoryGb <= 24 ? 0 : totalMemoryGb <= 47 ? 1 : 2;
  const cpuRank = cpuThreads < 12 ? 0 : cpuThreads < 16 ? 1 : 2;
  return RANK_MODE[Math.min(memoryRank, cpuRank)];
}

export function resolveResourceMode(selectedMode, totalMemoryGb, cpuThreads) {
  const recommended = recommendedResourceMode(totalMemoryGb, cpuThreads);
  const selected = ['auto', 'low', 'medium', 'high'].includes(selectedMode) ? selectedMode : 'auto';
  const effective = selected === 'auto' ? recommended : selected;
  return {
    selected,
    effective,
    recommended,
    supported: MODE_RANK[effective] <= MODE_RANK[recommended],
    limits: { ...RESOURCE_MODE_LIMITS[effective] },
  };
}

export function resourceAdmissionDecision({
  memoryUsedPercent,
  cpuPercent,
  pausedForMemory = false,
  millisecondsSinceLastStart = Number.POSITIVE_INFINITY,
  startSpacingMs = 10_000,
}) {
  const memoryPaused = pausedForMemory
    ? memoryUsedPercent >= 70
    : memoryUsedPercent >= 80;
  if (memoryPaused) return { allowed: false, reason: 'memory_pressure', memoryPaused: true };
  if (cpuPercent >= 85) return { allowed: false, reason: 'cpu_pressure', memoryPaused: false };
  if (millisecondsSinceLastStart < startSpacingMs) {
    return { allowed: false, reason: 'container_start_spacing', memoryPaused: false };
  }
  return { allowed: true, reason: 'resources_available', memoryPaused: false };
}

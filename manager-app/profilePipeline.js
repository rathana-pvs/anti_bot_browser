export function shuffledProfileOrder(profileIds, previousLastProfile = null, random = Math.random) {
  const order = [...new Set(profileIds || [])];
  for (let index = order.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(random() * (index + 1));
    [order[index], order[swapIndex]] = [order[swapIndex], order[index]];
  }
  if (order.length > 1 && previousLastProfile && order[0] === previousLastProfile) {
    const swapIndex = 1 + Math.floor(random() * (order.length - 1));
    [order[0], order[swapIndex]] = [order[swapIndex], order[0]];
  }
  return order;
}

export function orderedDueExecutions(queue, nowMs = Date.now()) {
  const due = [];
  for (const [batchIndex, batch] of (queue?.daily_batches || []).entries()) {
    const profileOrder = batch.profile_execution_order || batch.target_profiles || [];
    const profileRanks = new Map(profileOrder.map((profileId, index) => [profileId, index]));
    for (const [postIndex, post] of (batch.posts || []).entries()) {
      for (const execution of post.executions || []) {
        if (!['pending', 'ready'].includes(execution.status)) continue;
        if (Date.parse(execution.scheduled_at || '') > nowMs) continue;
        due.push({
          execution,
          batchIndex,
          postIndex,
          profileRank: profileRanks.get(execution.profile_id) ?? Number.MAX_SAFE_INTEGER,
        });
      }
    }
  }
  due.sort((left, right) => (
    left.batchIndex - right.batchIndex
    || left.postIndex - right.postIndex
    || left.profileRank - right.profileRank
    || Date.parse(left.execution.scheduled_at || '') - Date.parse(right.execution.scheduled_at || '')
  ));
  return due.map((item) => item.execution);
}

export function countBufferedPreparations(queue, exceptExecutionId = null) {
  let count = 0;
  for (const batch of queue?.daily_batches || []) {
    for (const post of batch.posts || []) {
      for (const execution of post.executions || []) {
        if (execution.execution_id === exceptExecutionId) continue;
        if (execution.status === 'preparing' || execution.status === 'ready') count += 1;
      }
    }
  }
  return count;
}

export function hasBufferedPreparation(queue, exceptExecutionId = null) {
  return countBufferedPreparations(queue, exceptExecutionId) > 0;
}

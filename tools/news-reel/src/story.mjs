// Shared by the renderer and composition: invalid copy fails before encoding.
export function validateStory(story) {
  if (!story || typeof story !== 'object') throw new Error('A story object is required');
  for (const [key, max] of [['brand', 28], ['category', 22], ['source', 100], ['date', 48]]) {
    if (typeof story[key] !== 'string' || !story[key].trim() || story[key].length > max) {
      throw new Error(`${key} must contain 1–${max} characters`);
    }
  }
  if (!/^#[0-9a-f]{6}$/i.test(story.accent)) throw new Error('accent must be a six-digit hex color');
  if (!Array.isArray(story.scenes) || story.scenes.length < 3 || story.scenes.length > 6) throw new Error('Use 3–6 scenes');
  for (const scene of story.scenes) {
    for (const [key, max] of [['label', 28], ['title', 72], ['body', 150]]) {
      if (typeof scene[key] !== 'string' || !scene[key].trim() || scene[key].length > max || scene[key].split(/\s+/).some(word => word.length > 24)) {
        throw new Error(`Scene ${key} is empty, too long, or contains an unbreakable word`);
      }
    }
    if (!Number.isInteger(scene.seconds) || scene.seconds < 3 || scene.seconds > 12) throw new Error('Scene duration must be 3–12 whole seconds');
    const words = `${scene.title} ${scene.body}`.trim().split(/\s+/).length;
    if (words > scene.seconds * 4) throw new Error('Too much text for the scene duration; allow at most 4 words per second');
    if (scene.image !== undefined && (typeof scene.image !== 'string' || !/^media\/[a-zA-Z0-9_-]+\.(png|jpe?g|webp)$/i.test(scene.image))) throw new Error('Images must be local media/name.png, .jpg or .webp paths');
  }
  const duration = story.scenes.reduce((total, scene) => total + scene.seconds, 0);
  if (duration > 60) throw new Error('Keep the story within 60 seconds');
  return story;
}
/**
 * @param {{scenes: Array<{label: string, title: string, body: string, seconds: number, image?: string}>}} story
 * @param {number} fps
 * @returns {Array<{label: string, title: string, body: string, seconds: number, image?: string, from: number, durationInFrames: number}>}
 */
export function timeline(story, fps = 30) {
  validateStory(story);
  let from = 0;
  return story.scenes.map(scene => {
    const durationInFrames = scene.seconds * fps;
    const entry = {...scene, from, durationInFrames};
    from += durationInFrames;
    return entry;
  });
}

import {readFile, mkdir, mkdtemp, rm, copyFile, constants, access} from 'node:fs/promises';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {bundle} from '@remotion/bundler';
import {selectComposition, renderMedia, renderStill} from '@remotion/renderer';
import {validateStory} from './src/story.mjs';

const root = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const preview = args.includes('--preview');
const positional = args.filter(arg => arg !== '--preview');
if (positional.length > 2) throw new Error('Usage: npm run render -- [story.json] [output.mp4] [--preview]');
const story = validateStory(JSON.parse(await readFile(resolve(positional[0] ?? join(root,'sample.json')), 'utf8')));
for (const scene of story.scenes) if (scene.image) await access(join(root, 'public', scene.image));
const output = resolve(positional[1] ?? join(root,'output',preview ? 'news-preview.mp4' : 'news-reel.mp4'));
if (!output.endsWith('.mp4')) throw new Error('Output must end in .mp4');
await mkdir(dirname(output), {recursive:true});
const work = await mkdtemp(join(tmpdir(),'news-reel-'));
try {
  const serveUrl = await bundle({entryPoint:join(root,'src/index.tsx'), publicDir:join(root,'public'), outDir:join(work,'bundle')});
  const browser = process.env.REMOTION_BROWSER_EXECUTABLE ? {browserExecutable:process.env.REMOTION_BROWSER_EXECUTABLE} : {};
  const composition = await selectComposition({serveUrl,id:'NewsReel',inputProps:story,...browser});
  const scale = preview ? 1/3 : 1;
  console.log(`Rendering ${composition.durationInFrames / composition.fps}s at ${1080*scale}×${1920*scale}`);
  let last = -1;
  await renderMedia({serveUrl,composition,inputProps:story,outputLocation:join(work,'render.mp4'),codec:'h264',pixelFormat:'yuv420p',concurrency:2,scale,crf:20,...browser,onProgress:({progress})=>{const step=Math.floor(progress*10);if(step!==last){last=step;console.log(`${step*10}%`);}}});
  // Final local FFmpeg pass provides portable H.264 MP4 with fast-start metadata.
  execFileSync('ffmpeg',['-v','error','-i',join(work,'render.mp4'),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',join(work,'final.mp4')],{timeout:120000});
  const probe=JSON.parse(execFileSync('ffprobe',['-v','error','-show_streams','-show_format','-of','json',join(work,'final.mp4')],{encoding:'utf8',timeout:30000}));
  const video=probe.streams.find(s=>s.codec_type==='video');
  if(video?.codec_name!=='h264' || video.width!==1080*scale || video.height!==1920*scale || video.avg_frame_rate!=='30/1' || Math.abs(Number(probe.format.duration)-composition.durationInFrames/30)>1/30) throw new Error('Output verification failed');
  execFileSync('ffmpeg',['-v','error','-xerror','-i',join(work,'final.mp4'),'-f','null','-'],{timeout:120000});
  await renderStill({serveUrl,composition,inputProps:story,frame:45,output:join(work,'poster.png'),scale,...browser});
  await copyFile(join(work,'final.mp4'),output,constants.COPYFILE_EXCL);
  await copyFile(join(work,'poster.png'),output.replace(/\.mp4$/,'.png'),constants.COPYFILE_EXCL);
  console.log(`Verified: ${output}`);
} finally {await rm(work,{recursive:true,force:true});}

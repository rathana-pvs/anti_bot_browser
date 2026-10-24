// Backend-owned process: render one scene at a time under the app's memory cap.
import {readFile, writeFile, unlink} from 'node:fs/promises';
import {dirname, resolve, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {selectComposition, renderMedia} from '@remotion/renderer';
const [manifest,output,ffmpeg]=process.argv.slice(2);
const root=dirname(fileURLToPath(import.meta.url));
const story=JSON.parse(await readFile(manifest,'utf8'));
const {executablePath}=JSON.parse(await readFile(resolve(root,'browser.json'),'utf8'));
const clips=[];
const isLens=story.template==='lens_report';
if(isLens){const runtime=JSON.parse(await readFile(resolve(root,'app-runtime.json'),'utf8'));if(!(runtime.lens_report_revisions ?? [runtime.lens_report]).includes(story.layoutRevision))throw new Error('Prepare the Lens Report runtime again.');}
let completed=0;
for (const [index,scene] of story.scenes.entries()) {
  const layers=await Promise.all(story.images[scene.imageIndex].map(async name=>{
    if(basename(name)!==name) throw new Error('Invalid prepared image');
    return 'data:image/png;base64,'+(await readFile(resolve(dirname(manifest),name))).toString('base64');
  }));
  const sprites=story.textSprites ? await Promise.all(story.textSprites[scene.imageIndex].map(async sprite=>{
    if(basename(sprite.file)!==sprite.file)throw new Error('Invalid prepared text layer');
    return {...sprite,src:'data:image/png;base64,'+(await readFile(resolve(dirname(manifest),sprite.file))).toString('base64')};
  })) : undefined;
  const photo=story.photoLayers?.[scene.imageIndex];
  const photoLayer=photo ? {...photo,...await (async()=>{
    for(const name of [photo.file,photo.background])if(basename(name)!==name)throw new Error('Invalid prepared photo');
    return {src:'data:image/png;base64,'+(await readFile(resolve(dirname(manifest),photo.file))).toString('base64'),
      backgroundSrc:'data:image/png;base64,'+(await readFile(resolve(dirname(manifest),photo.background))).toString('base64')};
  })()} : undefined;
  const props={...story,photoLayers:photoLayer?[photoLayer]:undefined,textSprites:sprites ? [sprites] : undefined,images:[layers],scenes:[{fromFrame:0,frames:scene.frames,imageIndex:0}],
    movements:[story.movements?.[scene.imageIndex] ?? 'still'],durationInFrames:scene.frames,progressFrom:completed,totalFrames:story.durationInFrames};
  const options={serveUrl:resolve(root,'app-bundle'),inputProps:props,browserExecutable:executablePath};
  const composition=await selectComposition({...options,id:'StudioDailyBrief'});
  const clip=`daily-scene-${index}.mp4`;
  await renderMedia({...options,composition,outputLocation:resolve(dirname(manifest),clip),codec:'h264',pixelFormat:'yuv420p',colorSpace:'bt709',concurrency:1,disallowParallelEncoding:true,crf:20,x264Preset:'veryfast',ffmpegOverride:({args})=>['-threads','1','-filter_threads','1',...args.slice(0,-1),'-threads','1',args.at(-1)],onProgress:({progress})=>{
    process.stdout.write(`out_time_us=${Math.round((completed+progress*(scene.hold ?? scene.frames))/story.fps*1000000)}\n`);
  }});
  clips.push(`file '${clip}'`);
  completed+=scene.hold ?? scene.frames;
}
if((isLens || story.flexibleScene) && story.transitionFrames && clips.length>1){
  let accumulated=resolve(dirname(manifest),'daily-scene-0.mp4');
  let offset=0;
  for(let index=1;index<clips.length;index++){
    offset+=(story.scenes[index-1].hold ?? story.scenes[index-1].frames);
    const next=resolve(dirname(manifest),`daily-scene-${index}.mp4`);
    const joined=index===clips.length-1?output:resolve(dirname(manifest),`lens-joined-${index}.mp4`);
    const transition=story.transitionFrames/story.fps;
    const filter=`[0:v]settb=AVTB,setpts=PTS-STARTPTS[a];[1:v]settb=AVTB,setpts=PTS-STARTPTS[b];[a][b]xfade=transition=fade:duration=${transition}:offset=${offset/story.fps},format=yuv420p[v]`;
    execFileSync(ffmpeg,['-v','error','-nostdin','-filter_complex_threads','1','-i',accumulated,'-i',next,'-filter_complex',filter,'-map','[v]','-c:v','libx264','-threads','1','-preset','veryfast','-crf','20','-color_range','tv','-r',String(story.fps),'-frames:v',String(offset+(story.scenes[index].hold ?? story.scenes[index].frames)+(index<clips.length-1?story.transitionFrames:0)),'-y',joined],{timeout:120000});
    await unlink(accumulated);await unlink(next);accumulated=joined;
  }
}else{
  const list=resolve(dirname(manifest),'daily-concat.txt');
  await writeFile(list,clips.join('\n'));
  execFileSync(ffmpeg,['-v','error','-nostdin','-f','concat','-safe','1','-i',list,'-c','copy','-y',output],{timeout:120000});
}

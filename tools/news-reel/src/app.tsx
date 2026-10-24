import React from 'react';
import {photoMotionState} from './photo-motion.mjs';
import {lensTextState} from './lens-motion.mjs';
import {AbsoluteFill, Composition, Img, Sequence, interpolate, registerRoot, useCurrentFrame, useVideoConfig} from 'remotion';
type Scene = {fromFrame: number; frames: number; imageIndex: number};
type TextSprite = {src: string; x:number; y:number; width:number; height:number; kind:'title'|'body'; index:number};
type Box={x:number;y:number;width:number;height:number};
type PhotoLayer={src:string;backgroundSrc:string;viewport:Box;bounds:Box};
type Props = {flexibleScene?:boolean;photoLayers?:PhotoLayer[];motionIntensity?:string;showProgress?: boolean; progressTrack?: string;layoutRevision?: number; textSprites?: TextSprite[][]; textAnimation?: string; template?: 'daily_brief' | 'lens_report'; movements?: string[]; progressFrom?: number; totalFrames?: number; width: number; height: number; fps: number; durationInFrames: number; motion: boolean; accent: string; images: string[][]; scenes: Scene[]};
const LayerScene: React.FC<{layers: string[]; motion: boolean; lens?: boolean; movement?: string; frames: number; sprites?: TextSprite[]; mode?:string; width:number;photoLayer?:PhotoLayer;intensity?:string}> = ({layers, motion, lens, movement='still', frames, sprites, mode='auto', width,photoLayer,intensity='balanced'}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const entrance = motion ? interpolate(frame,[0,lens?Math.round(fps*.5):12],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'}) : 1;
  const progress=interpolate(frame,[0,Math.max(1,frames-1)],[0,1],{extrapolateRight:'clamp'});
  const scale=movement==='zoom_out'?1.025-.025*progress:movement==='zoom_in'?1+.025*progress:movement.startsWith('pan_')?1.025:1;
  const pan=movement==='pan_left'?(1-2*progress)*.008:movement==='pan_right'?(2*progress-1)*.008:0;
  const nativeState=photoMotionState({frame,frames,movement,intensity,enabled:motion});
  const titleCount=sprites?.filter(sprite=>sprite.kind==='title').length ?? 0;
  return <AbsoluteFill>
    {layers.map((src,index)=>photoLayer && index===0 ? <AbsoluteFill key={index}>
      <Img src={photoLayer.backgroundSrc} style={{position:'absolute',width:'100%',height:'100%'}}/>
      <div style={{position:'absolute',left:photoLayer.viewport.x,top:photoLayer.viewport.y,width:photoLayer.viewport.width,height:photoLayer.viewport.height,overflow:'hidden'}}>
        <AbsoluteFill style={{transform:`translateX(${nativeState.x*photoLayer.viewport.width}px) scale(${nativeState.scale})`}}>
          <Img src={photoLayer.src} style={{position:'absolute',left:photoLayer.bounds.x-photoLayer.viewport.x,top:photoLayer.bounds.y-photoLayer.viewport.y,width:photoLayer.bounds.width,height:photoLayer.bounds.height}}/>
        </AbsoluteFill>
      </div>
    </AbsoluteFill> : sprites && index===2 ? null : <Img key={index} src={src} style={{position:'absolute',width:'100%',height:'100%',opacity:index===2?entrance:1,transform:lens && index===0 ? `translateX(${pan*100}%) scale(${scale})` : index===2 ? `translateY(${(1-entrance)*(lens?0:8)}px)` : undefined}}/>)}
    {sprites?.map((sprite,index)=>{
      const state=lensTextState({frame,fps,kind:sprite.kind,index:sprite.index,titleCount,mode,enabled:motion,width,strength:photoLayer?({subtle:1,balanced:1.5,dramatic:2}[intensity] ?? 1.5):1});
      return <div key={index} style={{position:'absolute',left:sprite.x,top:sprite.y,width:sprite.width,height:sprite.height,overflow:'hidden'}}>
        <Img src={sprite.src} style={{display:'block',width:'100%',height:'100%',opacity:state.opacity,transform:`translate(${state.x}px,${state.y}px)`}}/>
      </div>;
    })}
  </AbsoluteFill>;

};
const AppReel: React.FC<Props> = props => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background:'#0b171e'}}>
    {props.scenes.map((scene,i)=><Sequence key={i} from={scene.fromFrame} durationInFrames={scene.frames}><LayerScene layers={props.images[scene.imageIndex]} motion={props.motion} lens={props.template==='lens_report'} movement={props.movements?.[scene.imageIndex]} frames={scene.frames} sprites={props.textSprites?.[scene.imageIndex]} mode={props.textAnimation} width={props.width} photoLayer={props.photoLayers?.[scene.imageIndex]} intensity={props.motionIntensity}/></Sequence>)}
    {(props.flexibleScene || props.template==='lens_report' && props.layoutRevision===2) && props.showProgress && <div style={{position:'absolute',left:0,right:0,bottom:0,height:Math.max(2,props.width/180),background:props.progressTrack}}>
      <div style={{height:'100%',width:`${Math.min(1,Math.max(0,(frame+(props.progressFrom ?? 0))/Math.max(1,(props.totalFrames ?? props.durationInFrames)-1)))*100}%`,background:props.accent}}/>
    </div>}
    {props.motion && props.template!=='lens_report' && !props.flexibleScene && <div style={{position:'absolute',left:0,top:0,height:Math.max(2,props.width/180),width:`${(frame+(props.progressFrom ?? 0))/Math.max(1,(props.totalFrames ?? props.durationInFrames)-1)*100}%`,background:props.accent}}/>}
  </AbsoluteFill>;
};
const defaults: Props = {width:1080,height:1920,fps:30,durationInFrames:300,motion:true,accent:'#caff69',images:[],scenes:[]};
registerRoot(()=> <Composition id="StudioDailyBrief" component={AppReel} width={1080} height={1920} fps={30} durationInFrames={300} defaultProps={defaults} calculateMetadata={({props})=>({width:props.width,height:props.height,fps:props.fps,durationInFrames:props.durationInFrames})}/>);

import React from 'react';
import {AbsoluteFill, Composition, Img, Sequence, interpolate, registerRoot, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import sample from '../sample.json';
import {timeline, validateStory} from './story.mjs';

type Scene = {label: string; title: string; body: string; seconds: number; image?: string};
type Story = Omit<typeof sample, 'scenes'> & {scenes: Scene[]};
const SceneView: React.FC<{scene: Scene; accent: string; index: number}> = ({scene, accent, index}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const entrance = interpolate(frame, [0, 16], [0, 1], {extrapolateLeft:'clamp', extrapolateRight:'clamp'});
  const zoom = interpolate(frame, [0, scene.seconds * fps], [1, 1.045]);
  return <AbsoluteFill style={{opacity: entrance}}>
    <div style={{position:'absolute', top:250, left:72, right:96, height:690, overflow:'hidden', borderRadius:24, background:'#172b33'}}>
      {scene.image ? <Img src={staticFile(scene.image)} style={{width:'100%', height:'100%', objectFit:'cover', transform:`scale(${zoom})`}}/> :
        <div style={{width:'100%',height:'100%',transform:`scale(${zoom})`, background:'linear-gradient(145deg,#213f4b,#101f28)'}}>
          <svg viewBox="0 0 900 690" width="100%" height="100%" aria-label="Abstract library illustration">
            <circle cx="690" cy="165" r="200" fill={accent} opacity="0.08"/>
            {[0,1,2].map(row => <g key={row} transform={`translate(95 ${160+row*145})`}>
              {[0,1,2,3,4,5,6,7].map(book => <rect key={book} x={book*82} y={-((book+row)%3)*12} width="57" height={100+((book+row)%3)*12} rx="7" fill={[accent,'#719b9e','#e0e5d6'][((book+row+index)%3)]} opacity={0.6}/>) }
              <rect x="-20" y="110" width="715" height="12" rx="6" fill="#789196"/>
            </g>)}
          </svg>
          <div style={{position:'absolute',bottom:24,left:28,color:'#bdcfd2',fontSize:20,letterSpacing:3}}>ILLUSTRATION / SAMPLE STORY</div>
        </div>}
    </div>
    <div style={{position:'absolute',top:1005,left:72,right:112,transform:`translateY(${(1-entrance)*24}px)`}}>
      <div style={{fontSize:24,letterSpacing:4,fontWeight:700,color:accent,marginBottom:25}}>{scene.label}</div>
      <div style={{fontSize:scene.title.length > 50 ? 72 : 84,lineHeight:1.06,fontWeight:800,letterSpacing:-3,overflowWrap:'break-word'}}>{scene.title}</div>
      <div style={{width:68,height:5,background:accent,margin:'32px 0'}}/>
      <div style={{fontSize:36,lineHeight:1.38,color:'#c2cdd3',overflowWrap:'break-word'}}>{scene.body}</div>
    </div>
  </AbsoluteFill>;
};
const NewsReel: React.FC<Story> = story => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  return <AbsoluteFill style={{background:'#0b171e',color:'#f5f5ef',fontFamily:'Arial, sans-serif'}}>
    {timeline(story, fps).map((scene, index) => <Sequence key={index} from={scene.from} durationInFrames={scene.durationInFrames}><SceneView scene={scene} accent={story.accent} index={index}/></Sequence>)}
    <div style={{position:'absolute',top:122,left:72,right:112,display:'flex',justifyContent:'space-between',alignItems:'center',borderBottom:'1px solid #3a4b52',paddingBottom:28}}>
      <div style={{fontSize:27,fontWeight:800,letterSpacing:2}}>{story.brand}</div>
      <div style={{fontSize:20,color:'#0b171e',background:story.accent,padding:'12px 17px',borderRadius:4,fontWeight:700}}>{story.category}</div>
    </div>
    <div style={{position:'absolute',bottom:230,left:72,right:112,borderTop:'1px solid #3a4b52',paddingTop:23}}>
      <div style={{fontSize:23,color:'#c2cdd3',lineHeight:1.4,overflowWrap:'break-word'}}>{story.source}</div>
      <div style={{fontSize:18,color:'#8fa5ad',marginTop:12,letterSpacing:1}}>{story.date}</div>
      <div style={{display:'flex',gap:10,marginTop:25}}>{timeline(story,fps).map((scene,index) => <div key={index} style={{flex:1,height:4,background:'#2a3d45'}}><div style={{width:`${interpolate(frame,[scene.from,scene.from+scene.durationInFrames],[0,100],{extrapolateLeft:'clamp',extrapolateRight:'clamp'})}%`,height:'100%',background:story.accent}}/></div>)}</div>
    </div>
    <div style={{position:'absolute',top:0,left:0,width:`${frame/(durationInFrames-1)*100}%`,height:7,background:story.accent}}/>
  </AbsoluteFill>;
};
const Root = () => <Composition id="NewsReel" component={NewsReel} width={1080} height={1920} fps={30} durationInFrames={900} defaultProps={sample} calculateMetadata={({props}) => {validateStory(props); return {durationInFrames:timeline(props).reduce((sum,s)=>sum+s.durationInFrames,0)};}}/>;
registerRoot(Root);

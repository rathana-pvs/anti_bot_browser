import { useEffect, useState } from 'react';
import type { ContentFit, ReelComposition } from '../types/reelCreator';
import { reelApi } from '../services/reelCreator';
import { contentFitKey } from '../services/templateContent';

export function useContentFit(value: ReelComposition, active: boolean) {
  const key=contentFitKey(value);
  const [state,setState]=useState<{key:string;result:ContentFit|null;error:string}>({key:'',result:null,error:''});
  useEffect(()=>{
    if(!active)return;
    const controller=new AbortController();
    const timer=window.setTimeout(()=>{
      void reelApi.contentFit(value,controller.signal).then(result=>{
        if(!controller.signal.aborted)setState({key,result,error:''});
      }).catch(error=>{
        if(!controller.signal.aborted)setState({key,result:null,error:error instanceof Error?error.message:'Could not check content fit.'});
      });
    },400);
    return ()=>{window.clearTimeout(timer);controller.abort();};
    // All text, scene order and reading/layout settings are included in key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[key,active]);
  return {fit:state.key===key?state.result:null,error:state.key===key?state.error:'',checking:active&&state.key!==key};
}

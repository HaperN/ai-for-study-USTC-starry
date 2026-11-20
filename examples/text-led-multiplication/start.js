(function(){
 const d=window.TextLedSampleData,root=document.getElementById('lesson');let controller=null,lastZ=null;
 const scene=ComplexMultiplication.createScene(document.getElementById('scene-host'),{onChange(state){
  if(lastZ&&(lastZ[0]!==state.z[0]||lastZ[1]!==state.z[1])&&controller&&controller.getState().mode==='guided'&&!controller.audio.paused)controller.pauseForExploration();
  lastZ=state.z.slice();
 }});
 controller=TextLedLesson.mount(root,{lesson:d.lesson,manifest:d.manifest,scene});
 window.addEventListener('pagehide',()=>{controller.destroy();scene.destroy();},{once:true});
})();

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtMultimedia
Dialog{
 id:editor;objectName:"storyboardVideoDialog";property var desk;property string shotId:"";property string owner:"";property string selectedGenerationId:"";property var settings:({});property var selectedFrameIds:[];property string errorText:""
    SubmissionState{id:contentSubmission;owner:editor;operation:"";target:({unit_id:editor.owner||null})}

 readonly property var targetShot:desk.shots.find(s=>s.id===shotId)||({})
 readonly property var generations:targetShot.generations||[]
 readonly property bool generationStale:generation.stale===true||(desk.checkReport.revision===desk.board.revision&&(desk.checkReport.generations||[]).some(g=>g.generation_id===generation.id&&g.stale===true))
 readonly property var generation:generations.find(g=>g.id===selectedGenerationId)||({})
 title:"本镜视频生成与采用";modal:true;anchors.centerIn:parent;width:Math.min(800,desk.width-24);height:Math.min(780,desk.height-24)
 function openFor(){desk.playing=false;shotId=desk.shot.id;owner=desk.unitId;selectedGenerationId=(desk.shot.generations||[]).slice(-1)[0]?.id||"";prompt.text=desk.shot.title||"";duration.value=Math.max(4,Math.min(15,Math.ceil(desk.shot.duration_ms/1000)));errorText="";selectedFrameIds=(desk.shot.keyframes||[]).filter(f=>!!f.style_path).map(f=>f.id);var selected=desk.shot.selected_video||{};videoPath.text=selected.path||"";inPoint.value=selected.in_ms||0;outPoint.value=selected.out_ms||desk.shot.duration_ms;fit.currentIndex=selected.fit==="retime"?1:0;open();desk.request("read_storyboard_video_settings",{})}
 function applySettings(value){settings=value;base.text=value.base_url||"";videoModel.currentIndex=Math.max(0,videoModel.model.indexOf(value.model));resolution.currentIndex=Math.max(0,resolution.model.indexOf(value.resolution));keyEnv.text=value.key_env||"MINIMAX_API_KEY";key.text="";clearKey.checked=false}
 function closeSettings(){settingsDialog.close()}
 function updateAfter(operation){if(operation==="prepare_storyboard_video")selectedGenerationId=generations.slice(-1)[0]?.id||""}
 function status(value){return({prepared:"输入已准备",submitting:"正在提交",submitted:"已提交",queued:"排队中",running:"生成中",succeeded:"生成成功",failed:"生成失败",cancelled:"已取消"})[value]||value||"未准备"}
 function adoptGenerated(){if(!generation.output_path)return;videoPath.text=generation.output_path;inPoint.value=0;outPoint.value=generation.media?.duration_ms||targetShot.duration_ms;fit.currentIndex=outPoint.value===targetShot.duration_ms?0:1;section.currentIndex=1}
 contentItem:ColumnLayout{
        SubmissionFeedback{state:contentSubmission}

  Label{text:editor.targetShot.title||"";font.bold:true;Layout.fillWidth:true;wrapMode:Text.Wrap}
  Label{visible:!!editor.errorText;text:editor.errorText;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
  RowLayout{ComboBox{id:section;objectName:"storyboardVideoSection";model:["准备与生成","选段采用"]}Button{objectName:"storyboardVideoService";text:"视频服务设置";onClicked:settingsDialog.open()}}
  ScrollView{id:scroll;Layout.fillWidth:true;Layout.fillHeight:true;contentWidth:availableWidth;clip:true
   ColumnLayout{width:scroll.availableWidth
    ColumnLayout{visible:section.currentIndex===0;Layout.fillWidth:true
     ComboBox{id:intent;objectName:"storyboardVideoMode";property var values:["reference_motion","endpoints"];model:["参考运动＋外观","指定首帧 / 首尾帧"]}
     RowLayout{ComboBox{id:videoModel;objectName:"storyboardVideoModel";model:editor.settings.models||["MiniMax-H3","MiniMax-H3-Highspeed","MiniMax-H3-Lite","MiniMax-H3-Max"]}ComboBox{id:resolution;objectName:"storyboardVideoResolution";model:["768P","2K","480P"]}Label{text:"生成秒数"}SpinBox{id:duration;objectName:"storyboardVideoDuration";from:4;to:15;value:4;editable:true}}
     Label{text:"生成时长单独选择，不改本镜时长。先准备实际输入，检查后再提交一次请求。";wrapMode:Text.Wrap;Layout.fillWidth:true}
     TextArea{id:prompt;objectName:"storyboardVideoPrompt";placeholderText:"本镜视频生成说明";wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.preferredHeight:90}
     Label{text:intent.currentIndex===0?"选择1至9张风格参考帧":"选择1至2张首尾参考帧（按镜内时间排序）";wrapMode:Text.Wrap;Layout.fillWidth:true}
     Flow{Layout.fillWidth:true;spacing:4;Repeater{model:editor.targetShot.keyframes||[];CheckBox{required property var modelData;visible:!!modelData.style_path;text:desk.seconds(modelData.offset_ms);checked:editor.selectedFrameIds.includes(modelData.id);onToggled:{var ids=editor.selectedFrameIds.filter(id=>id!==modelData.id);if(checked)ids.push(modelData.id);editor.selectedFrameIds=(editor.targetShot.keyframes||[]).filter(f=>ids.includes(f.id)).map(f=>f.id)}}}}
     Button{objectName:"storyboardPrepareVideo";text:"准备并查看实际输入";enabled:desk.approved&&!desk.busy&&!!prompt.text.trim()&&editor.selectedFrameIds.length>0&&editor.selectedFrameIds.length<=(intent.currentIndex===0?9:2);onClicked:desk.mediaAction("prepare_storyboard_video",{unit_id:editor.owner,shot_id:editor.shotId,revision:desk.board.revision,mode:intent.values[intent.currentIndex],duration_ms:duration.value*1000,model:videoModel.model[videoModel.currentIndex],resolution:resolution.model[resolution.currentIndex],prompt:prompt.text,frame_ids:editor.selectedFrameIds})}
     Label{visible:!desk.approved;text:"视频生成前请先定案本场分镜并准备风格帧。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.muted}
     ComboBox{objectName:"storyboardGenerationChoice";model:editor.generations.map((g,i)=>({id:g.id,label:"第"+(i+1)+"次 · "+editor.status(g.status)+" · "+(g.duration_ms/1000)+"秒"}));textRole:"label";Layout.fillWidth:true;currentIndex:model.findIndex(g=>g.id===editor.selectedGenerationId);onActivated:editor.selectedGenerationId=model[currentIndex].id}
     Label{objectName:"storyboardGenerationStatus";visible:!!editor.generation.id;text:editor.status(editor.generation.status)+(editor.generationStale?" · 依据已变化，需重新核对":"");wrapMode:Text.Wrap;Layout.fillWidth:true}
     Label{visible:!!editor.generation.id;text:(editor.generation.model||"")+" · "+(editor.generation.resolution||"")+" · "+(editor.generation.duration_ms/1000)+"秒\n"+(editor.generation.prompt||"");wrapMode:Text.Wrap;Layout.fillWidth:true}
     Repeater{model:editor.generation.inputs||[];ColumnLayout{required property var modelData;Layout.fillWidth:true
      Label{text:modelData.kind==="motion"?"实际干净运动参考":"实际风格参考";font.bold:true}
      Image{visible:modelData.kind==="style";source:bridge&&modelData.kind==="style"&&modelData.path?bridge.storyboardImageUrl(modelData.path):"";fillMode:Image.PreserveAspectFit;Layout.fillWidth:true;Layout.preferredHeight:130}
      Button{visible:modelData.kind==="motion";text:"播放实际运动参考";onClicked:{inputPreview.path=modelData.path;inputPreview.open()}}
     }}
     Label{visible:!!editor.generation.error;text:editor.generation.error||"";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
     Flow{Layout.fillWidth:true;spacing:5
      Button{objectName:"storyboardSubmitVideo";text:"确认输入并提交一次生成";enabled:editor.generation.status==="prepared"&&!editor.generationStale&&editor.settings.has_api_key===true&&!desk.busy;onClicked:desk.mediaAction("submit_storyboard_video",{unit_id:editor.owner,shot_id:editor.shotId,generation_id:editor.generation.id,revision:desk.board.revision})}
      Button{objectName:"storyboardRefreshVideo";text:"查询同一任务";enabled:!!editor.generation.provider_task_id&&!desk.busy&&editor.generation.status!=="succeeded";onClicked:desk.mediaAction("refresh_storyboard_video",{unit_id:editor.owner,shot_id:editor.shotId,generation_id:editor.generation.id,revision:desk.board.revision})}
      Button{objectName:"storyboardUseGeneratedVideo";text:"查看选段并采用…";enabled:!!editor.generation.output_path&&!desk.busy;onClicked:editor.adoptGenerated()}
     }
     Label{visible:editor.generation.status==="succeeded";text:"生成结果尚未自动采用；请检查素材并明确选择入出点。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.muted}
    }
    ColumnLayout{visible:section.currentIndex===1;Layout.fillWidth:true
     RowLayout{Layout.fillWidth:true;TextField{id:videoPath;objectName:"storyboardAttachVideoPath";placeholderText:"本地视频文件";Layout.fillWidth:true}Button{objectName:"storyboardBrowseVideo";text:"浏览";onClicked:videoPicker.open()}}
     VideoOutput{id:localOutput;Layout.fillWidth:true;Layout.preferredHeight:180;fillMode:VideoOutput.PreserveAspectFit}
     MediaPlayer{id:localVideo;source:bridge&&videoPath.text?bridge.storyboardImageUrl(videoPath.text):"";videoOutput:localOutput;audioOutput:AudioOutput{muted:true}}
     RowLayout{Button{text:localVideo.playbackState===MediaPlayer.PlayingState?"暂停素材":"播放素材";onClicked:localVideo.playbackState===MediaPlayer.PlayingState?localVideo.pause():localVideo.play()}Label{text:"实际素材 "+desk.seconds(localVideo.duration)}Button{text:"使用素材全长";enabled:localVideo.duration>0;onClicked:{inPoint.value=0;outPoint.value=localVideo.duration}}}
     RowLayout{Label{text:"入点（毫秒）"}SpinBox{id:inPoint;objectName:"storyboardVideoIn";from:0;to:2147483647;editable:true}Label{text:"出点"}SpinBox{id:outPoint;objectName:"storyboardVideoOut";from:1;to:2147483647;editable:true}}
     ComboBox{id:fit;objectName:"storyboardVideoFit";model:["原速裁切（选段等于镜头时长）","变速适配本镜时长"]}
     RowLayout{Button{objectName:"storyboardAttachVideo";text:"采用所选视频区间";enabled:!!videoPath.text.trim()&&!desk.busy;onClicked:desk.mediaAction("attach_storyboard_video",{unit_id:editor.owner,shot_id:editor.shotId,revision:desk.board.revision,video_path:videoPath.text,in_ms:inPoint.value,out_ms:outPoint.value,fit:fit.currentIndex?"retime":"trim"})}Button{objectName:"storyboardDetachVideo";text:"取消采用";enabled:!!editor.targetShot.selected_video&&!desk.busy;onClicked:desk.mediaAction("detach_storyboard_video",{unit_id:editor.owner,shot_id:editor.shotId,revision:desk.board.revision})}}
    }
   }
  }
 }
 onClosed:{localVideo.pause();inputPreview.close();settingsDialog.close()}
 FileDialog{id:videoPicker;title:"选择采用视频";nameFilters:["视频 (*.mp4 *.mov *.mkv *.webm)"];onAccepted:videoPath.text=selectedFile.toString()}
 Dialog{id:inputPreview;property string path:"";title:"实际生成参考";modal:true;anchors.centerIn:parent;width:Math.min(680,desk.width-24);height:Math.min(470,desk.height-24)
  contentItem:ColumnLayout{VideoOutput{id:inputOutput;Layout.fillWidth:true;Layout.fillHeight:true;fillMode:VideoOutput.PreserveAspectFit}MediaPlayer{id:inputPlayer;source:bridge&&inputPreview.path?bridge.storyboardImageUrl(inputPreview.path):"";videoOutput:inputOutput;audioOutput:AudioOutput{muted:true}}RowLayout{Button{text:"播放 / 暂停";onClicked:inputPlayer.playbackState===MediaPlayer.PlayingState?inputPlayer.pause():inputPlayer.play()}Slider{Layout.fillWidth:true;from:0;to:Math.max(1,inputPlayer.duration);value:inputPlayer.position;onMoved:inputPlayer.position=Math.round(value)}}}onClosed:inputPlayer.pause()
 }
 Dialog{id:settingsDialog;objectName:"storyboardVideoSettingsDialog";title:"视频生成服务";modal:true;anchors.centerIn:parent;width:Math.min(570,desk.width-24)
  contentItem:ColumnLayout{
   Label{visible:!!editor.errorText;text:editor.errorText;color:desk.t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}
   TextField{id:base;objectName:"storyboardVideoBaseUrl";placeholderText:"服务地址";Layout.fillWidth:true}
   TextField{id:keyEnv;placeholderText:"密钥环境变量名";Layout.fillWidth:true}
   Label{text:editor.settings.has_api_key?"密钥已配置，不回显。":"尚未配置密钥。";wrapMode:Text.Wrap;Layout.fillWidth:true}
   TextField{id:key;objectName:"storyboardVideoApiKey";echoMode:TextInput.Password;placeholderText:"新密钥（留空保留现有）";Layout.fillWidth:true}
   CheckBox{id:clearKey;text:"清空已保存密钥"}
   Label{text:"密钥保存在本机独立配置中，不写入工程或聊天。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.muted}
   Button{objectName:"storyboardSaveVideoSettings";text:"保存服务设置";enabled:!desk.busy;onClicked:{var args={settings:{base_url:base.text,model:videoModel.model[videoModel.currentIndex],resolution:resolution.model[resolution.currentIndex],key_env:keyEnv.text},expected_revision:editor.settings.revision,reason:"修改视频生成服务"};if(clearKey.checked||key.text)args.api_key=clearKey.checked?"":key.text;desk.request("save_storyboard_video_settings",args)}}
  }
 }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Dialog{
 id:editor;objectName:"storyboardCameraDialog";property var desk;property string shotId:"";property int revision:0;property var selectedKey:null
    SubmissionState{id:contentSubmission;allowedActions:["set_camera_preset", "set_camera_keyframe", "set_camera_overlays"];authoredFields:[];identityKey:editor.shotId+":"+(editor.selectedKey?editor.selectedKey.id:"preset");owner:editor;operation:"apply_storyboard_changes";target:({unit_id:editor.desk.unitId||null});onRestoreRequested:function(a){var c=a.changes[0];editor.shotId=c.shot_id;editor.revision=a.revision;if(c.op==="set_camera_preset"){mode.currentIndex=0;preset.currentIndex=Math.max(0,preset.values.indexOf(c.preset));amount.text=String(c.amount||0);startTime.value=c.start_ms||0;endTime.value=c.end_ms||0;custom.checked=!!c.start;if(c.start)startPose.setPose(c.start);if(c.end)endPose.setPose(c.end)}else if(c.op==="set_camera_keyframe"){mode.currentIndex=1;pointTime.value=c.offset_ms;pointPose.setPose(c)} }}

 title:"摄影机与运镜";modal:true;anchors.centerIn:parent;width:Math.min(720,desk.width-24);height:Math.min(740,desk.height-24)
 function openFor(){shotId=desk.shot.id;revision=desk.board.revision;selectedKey=null;pointTime.value=Math.max(0,Math.min(desk.localTime,desk.shot.duration_ms-1));pointPose.setPose(Object.assign({},desk.cameraView.position||{},{yaw:desk.cameraView.yaw,pitch:desk.cameraView.pitch,fov_deg:desk.cameraView.fov_deg}));startTime.value=0;endTime.value=Math.max(0,desk.shot.duration_ms-1);preset.currentIndex=Math.max(0,preset.values.indexOf((desk.shot.camera||{}).preset||"fixed"));target.currentIndex=Math.max(0,target.model.findIndex(o=>o.id===(desk.shot.camera||{}).target_object_id));curve.currentIndex=Math.max(0,curve.values.indexOf((desk.shot.camera?.keys||[])[0]?.easing||"linear"));var hand=desk.shot.camera?.handheld;handheld.checked=!!hand;handAmplitude.text=String(hand?.amplitude===undefined?0.005:hand.amplitude);handFrequency.text=String(hand?.frequency_hz||1);handSeed.value=hand?.seed||0;focusTarget.currentIndex=Math.max(0,focusTarget.model.findIndex(o=>o.id===desk.shot.camera?.focus_target_object_id));custom.checked=false;amount.text=["pan","orbit","zoom"].includes(preset.values[preset.currentIndex])?"30":"0.25";var keys=(desk.shot.camera||{}).keys||[];startPose.setPose(keys[0]||{});endPose.setPose(keys[keys.length-1]||{});open();desk.request("load_storyboard_camera_presets",{})}
 function presetSettings(){var value={preset:preset.values[preset.currentIndex],amount:Number(amount.text),easing:curve.values[curve.currentIndex],handheld:handheld.checked?{amplitude:Number(handAmplitude.text),frequency_hz:Number(handFrequency.text),seed:handSeed.value}:null};if(custom.checked){value.start=startPose.current();value.end=endPose.current()}return value}
 contentItem:ColumnLayout{
        SubmissionFeedback{state:contentSubmission}

  Label{text:"机位视图使用二维站位、物体高度与俯仰做近似深度预演，不推断网格、手部接触或真实遮挡。";wrapMode:Text.Wrap;Layout.fillWidth:true}
  Label{visible:!!desk.message;text:desk.message;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
  ScrollView{id:scroll;Layout.fillWidth:true;Layout.fillHeight:true;contentWidth:availableWidth;clip:true
   ColumnLayout{width:scroll.availableWidth
    ComboBox{id:mode;objectName:"storyboardCameraMode";model:["应用运镜预设","编辑摄影机关键点"];Layout.fillWidth:true}
    ColumnLayout{visible:mode.currentIndex===0;Layout.fillWidth:true
     ComboBox{id:preset;objectName:"storyboardCameraPreset";property var values:["fixed","dolly","truck","crane","pan","zoom","follow","orbit","reveal","dolly_zoom"];model:["固定","推拉","横移","升降","摇摄","光学变焦","跟随","环绕","揭示","推轨变焦"];onActivated:amount.text=["pan","orbit","zoom"].includes(values[currentIndex])?"30":"0.25"}
     ComboBox{id:target;objectName:"storyboardCameraTarget";model:[{id:null,name:"无目标对象"}].concat(desk.board.objects||[]);textRole:"name";Layout.fillWidth:true}
     RowLayout{Label{text:"起点（镜内毫秒）"}SpinBox{id:startTime;from:0;to:2147483647;editable:true}Label{text:"终点"}SpinBox{id:endTime;from:0;to:2147483647;editable:true}}
     RowLayout{Label{text:"幅度（位置或角度）"}TextField{id:amount;objectName:"storyboardCameraAmount";text:"0.25";validator:DoubleValidator{}}}
     ComboBox{id:curve;objectName:"storyboardCameraCurve";property var values:["linear","ease_in","ease_out","ease_in_out"];model:["匀速","渐入","渐出","渐入渐出"]}
     CheckBox{id:custom;objectName:"storyboardCustomCameraPoses";text:"展开编辑起终机位"}
     StoryboardCameraPose{id:startPose;prefix:"storyboardCameraStart";visible:custom.checked;Layout.fillWidth:true;Component.onCompleted:setPose({})}
     StoryboardCameraPose{id:endPose;prefix:"storyboardCameraEnd";visible:custom.checked;Layout.fillWidth:true;Component.onCompleted:setPose({x:0.5,y:-0.5})}
     Button{objectName:"storyboardApplyCameraPreset";text:"应用到本镜";enabled:!desk.busy&&!desk.approved;onClicked:{var c=Object.assign({op:"set_camera_preset",shot_id:editor.shotId,start_ms:startTime.value,end_ms:endTime.value,target_object_id:target.model[target.currentIndex].id},editor.presetSettings());desk.savePhase2([c],editor.revision)}}
     Label{text:"工程运镜预设";font.bold:true}
     ComboBox{objectName:"storyboardSavedCameraPreset";model:desk.cameraPresets.presets||[];textRole:"name";Layout.fillWidth:true;onActivated:{var s=model[currentIndex].settings;preset.currentIndex=preset.values.indexOf(s.preset);amount.text=String(s.amount===undefined?0.25:s.amount);curve.currentIndex=Math.max(0,curve.values.indexOf(s.easing||"linear"));handheld.checked=!!s.handheld;handAmplitude.text=String(s.handheld?.amplitude===undefined?0.005:s.handheld.amplitude);handFrequency.text=String(s.handheld?.frequency_hz||1);handSeed.value=s.handheld?.seed||0;custom.checked=!!s.start||!!s.end;startPose.setPose(s.start||{});endPose.setPose(s.end||{})}}
     RowLayout{Layout.fillWidth:true;TextField{id:presetName;objectName:"storyboardCameraPresetName";placeholderText:"保存此设置的名称";Layout.fillWidth:true}Button{objectName:"storyboardSaveCameraPreset";text:"保存到工程";enabled:!!presetName.text.trim()&&!desk.busy;onClicked:desk.request("save_storyboard_camera_preset",{name:presetName.text.trim(),settings:editor.presetSettings(),expected_revision:desk.cameraPresets.revision,reason:"保存导演台运镜预设"})}}
    }
    ColumnLayout{visible:mode.currentIndex===1;Layout.fillWidth:true
     Repeater{model:(desk.shot.camera||{}).keys||[];RowLayout{required property var modelData;Layout.fillWidth:true;Label{text:desk.seconds(modelData.offset_ms);Layout.fillWidth:true}Button{objectName:"storyboardEditCameraPoint";property string keyframeId:modelData.id;text:"编辑";onClicked:{editor.selectedKey=modelData;pointTime.value=modelData.offset_ms;pointPose.setPose(modelData);pointCurve.currentIndex=Math.max(0,pointCurve.values.indexOf(modelData.easing||"linear"));editor.revision=desk.board.revision}}Button{text:"删除";enabled:!desk.busy&&!desk.approved;onClicked:desk.savePhase2([{op:"delete_camera_keyframe",shot_id:editor.shotId,keyframe_id:modelData.id}],desk.board.revision)}}}
     RowLayout{Label{text:"镜内时刻（毫秒）"}SpinBox{id:pointTime;objectName:"storyboardCameraPointTime";from:0;to:2147483647;editable:true}ComboBox{id:transition;model:["线性","保持"]}ComboBox{id:pointCurve;objectName:"storyboardCameraPointCurve";property var values:["linear","ease_in","ease_out","ease_in_out"];model:["匀速","渐入","渐出","渐入渐出"]}}
     StoryboardCameraPose{id:pointPose;prefix:"storyboardCameraPoint";Layout.fillWidth:true}
     Button{objectName:"storyboardSaveCameraPoint";text:"保存摄影机关键点";enabled:!desk.busy&&!desk.approved;onClicked:{var changes=[];if(editor.selectedKey&&editor.selectedKey.offset_ms!==pointTime.value)changes.push({op:"move_camera_keyframe",shot_id:editor.shotId,keyframe_id:editor.selectedKey.id,offset_ms:pointTime.value});changes.push(Object.assign({op:"set_camera_keyframe",shot_id:editor.shotId,offset_ms:pointTime.value,interpolation:transition.currentIndex?"hold":"linear",easing:pointCurve.values[pointCurve.currentIndex]},pointPose.current()));desk.savePhase2(changes,editor.revision)}}
    }
    Label{text:"叠加与对焦";font.bold:true}
    CheckBox{id:handheld;objectName:"storyboardHandheld";text:"轻微手持"}
    GridLayout{columns:2;visible:handheld.checked;Layout.fillWidth:true
     Label{text:"幅度（0–0.05）"}TextField{id:handAmplitude;objectName:"storyboardHandheldAmplitude";text:"0.005";validator:DoubleValidator{bottom:0;top:0.05} Layout.fillWidth:true}
     Label{text:"频率（次/秒）"}TextField{id:handFrequency;objectName:"storyboardHandheldFrequency";text:"1";validator:DoubleValidator{bottom:0.1;top:20} Layout.fillWidth:true}
     Label{text:"变化序号"}SpinBox{id:handSeed;from:0;to:2147483647;editable:true}
    }
    ComboBox{id:focusTarget;objectName:"storyboardFocusTarget";model:[{id:null,name:"按关键点对焦距离"}].concat(desk.board.objects||[]);textRole:"name";Layout.fillWidth:true}
    Label{text:"对焦高亮只表示二维半距离范围，不是真实景深或光学焦外。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.muted}
    Button{objectName:"storyboardSaveCameraOverlays";text:"保存手持与对焦";enabled:!desk.busy&&!desk.approved;onClicked:desk.savePhase2([{op:"set_camera_overlays",shot_id:editor.shotId,handheld:handheld.checked?{amplitude:Number(handAmplitude.text),frequency_hz:Number(handFrequency.text),seed:handSeed.value}:null,focus_target_object_id:focusTarget.model[focusTarget.currentIndex].id}],editor.revision)}
   }
  }
 }
}

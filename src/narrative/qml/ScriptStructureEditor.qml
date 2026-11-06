import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id:editor
    SubmissionState{id:contentSubmission;identityKey:editor.childKind+":"+(editor.object.id||"new");owner:editor;operation:"apply_screenplay_structure";target:({screenplay_id:editor.host.screenplay.id||null});onRestoreRequested:function(a){var c=a.actions[0]||{},v=c.changes||c;titleField.text=v.title||"";number.text=v.number||"1";brief.text=JSON.stringify(v.brief||{},null,2);duration.text=v.target_duration_seconds?String(v.target_duration_seconds):"";editor.childKind=c.op.indexOf("episode")>=0?"episode":"act";editor.object=Object.assign({},editor.object,{id:c.op.indexOf("create_")===0?"":c.episode_id||c.act_id||"",revision:c.expected_revision});if(c.op==="create_act")parentChoice.currentIndex=Math.max(0,editor.parentChoices.findIndex(function(x){return x.id===c.episode_id}))}}

    objectName:"scriptStructureEditor"
    property var host
    property string childKind:"act"
    property var object:({})
    property string errorText:""
    readonly property var t:host.t
    readonly property var parentChoices: {
        var out=[]
        if(host.screenplay.format==="single")out.push({kind:"screenplay",id:host.screenplay.id,name:"剧本根级"})
        else (host.doc.episodes||[]).forEach(function(e){out.push({kind:"episode",id:e.id,name:"集 · "+e.title})})
        if(childKind==="segment") (host.doc.acts||[]).forEach(function(a){out.push({kind:"act",id:a.id,name:"幕 · "+a.title})})
        return out
    }
    title:object.id?"编辑 / 移动结构对象":"新建结构对象"
    modal:true;anchors.centerIn:parent;width:Math.min(650,host.width-40)
    function openFor(kind,value){
        if(visible&&!contentSubmission.restoring)contentSubmission.begin();childKind=kind;object=value||({});titleField.text=object.title || "";number.text=object.number||"1";brief.text=JSON.stringify(object.brief||{},null,2);duration.text=object.target_duration_seconds?String(object.target_duration_seconds):"";errorText="";var entry=host.entries.find(function(e){return e.child_kind===kind&&e.child_id===editor.object.id});parentChoice.currentIndex=entry?Math.max(0,parentChoices.findIndex(function(p){return p.kind===entry.parent_kind&&p.id===entry.parent_id})):0;open()}
    function save(){
        try{
            var actions=[],parent=parentChoice.model[parentChoice.currentIndex]
            if(!object.id){
                var action={op:childKind==="episode"?"create_episode":"create_act",title:titleField.text,brief:JSON.parse(brief.text),target_duration_seconds:duration.text?Number(duration.text):null}
                if(childKind==="episode")action.number=number.text
                else action.episode_id=parent&&parent.kind==="episode"?parent.id:null
                actions.push(action)
            }else if(childKind==="segment"){
                host.act("update_script_segment",{segment_id:object.id,expected_revision:object.revision,changes:{title:titleField.text},reason:"结构页修改段落标题"})
            }else{
                var update={op:childKind==="episode"?"update_episode":"update_act",expected_revision:object.revision,changes:{title:titleField.text,brief:JSON.parse(brief.text),target_duration_seconds:duration.text?Number(duration.text):null}}
                update[childKind+"_id"]=object.id;if(childKind==="episode")update.changes.number=number.text;actions.push(update)
            }
            if(object.id&&childKind!=="episode"&&parent){var old=host.entries.find(function(e){return e.child_kind===editor.childKind&&e.child_id===editor.object.id});if(!old||old.parent_id!==parent.id||old.parent_kind!==parent.kind)actions.push({op:"move",child_kind:childKind,child_id:object.id,parent_kind:parent.kind,parent_id:parent.id,after_entry_id:null})}
            if(actions.length)host.structure(actions);else close()
        }catch(error){errorText=String(error)}
    }
    function remove(){var parent=parentChoice.model[parentChoice.currentIndex];var action={op:childKind==="episode"?"delete_episode":"delete_act",expected_revision:object.revision,move_to:parent?{parent_kind:parent.kind,parent_id:parent.id}:null};action[childKind+"_id"]=object.id;host.structure([action])}
    function moveSibling(entry,delta){var siblings=host.entries.filter(function(e){return e.parent_kind===entry.parent_kind&&e.parent_id===entry.parent_id}).sort(function(a,b){return a.position-b.position});var index=siblings.findIndex(function(e){return e.id===entry.id}),target=index+delta;if(target<0||target>=siblings.length)return;var after=delta<0?(target>0?siblings[target-1].id:null):siblings[target].id;host.structure([{op:"move",child_kind:entry.child_kind,child_id:entry.child_id,parent_kind:entry.parent_kind,parent_id:entry.parent_id,after_entry_id:after}])}
    contentItem:ColumnLayout{
        SubmissionFeedback{state:contentSubmission}

        enabled:!editor.host.structureBusy
        Label{visible:!!editor.errorText;text:editor.errorText;color:t.bad;wrapMode:Text.Wrap;Layout.fillWidth:true}
        Label{text:({episode:"集",act:"幕",segment:"段落"}[editor.childKind] || "结构")+" · "+(editor.object.title||"新建");color:t.muted}
        TextField{id:titleField;objectName:"scriptStructureTitle";placeholderText:"名称";Layout.fillWidth:true}
        TextField{id:number;visible:editor.childKind==="episode";placeholderText:"显示集号";Layout.fillWidth:true}
        Label{visible:editor.childKind!=="episode";text:"归属（删除非空幕时也作为搬移目标）";color:t.muted}
        ComboBox{id:parentChoice;objectName:"scriptStructureParent";visible:editor.childKind!=="episode"||!!editor.object.id;Layout.fillWidth:true;model:editor.parentChoices.filter(function(p){return p.id!==editor.object.id});textRole:"name"}
        TextField{id:duration;visible:editor.childKind!=="segment";placeholderText:"目标时长（秒）";validator:DoubleValidator{bottom:0.01} Layout.fillWidth:true}
        TextArea{id:brief;visible:editor.childKind!=="segment";Layout.fillWidth:true;Layout.preferredHeight:160;wrapMode:TextEdit.Wrap}
        RowLayout{Button{visible:!!editor.object.id&&editor.childKind!=="segment";text:"删除并搬移子项";onClicked:editor.remove()}Item{Layout.fillWidth:true}Button{text:"取消";onClicked:editor.close()}Button{objectName:"scriptStructureSave";text:"保存";onClicked:editor.save()}}
    }
}





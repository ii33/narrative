import QtQuick

QtObject {
    id: state
    required property var owner
    property string operation:""
    property var target:({})
    property string identityKey:""
    property var allowedActions:[]
    property var authoredFields:[]
    property bool closeOnAccepted:false
    property bool enabled:true
    property var controller:owner?(owner.host||owner.desk||owner):null
    property var app:owner?(owner.app||((owner.host||owner.desk||{}).app)):null
    readonly property string projectPath:app&&app.project?app.project.path||"":""
    readonly property bool active:enabled&&!!owner&&owner.visible&&!!app&&app.mode==="workspace"
    readonly property string targetKey:JSON.stringify(target)
    readonly property int priority:{var depth=0,item=owner;while(item){depth++;item=item.parent}return depth+(typeof owner.modal==="boolean"?1000:0)}
    property string formId:""
    property var submission:({})
    property var candidates:[]
    property string errorText:""
    property bool restoring:false
    property bool conflictDetached:false
    signal restoreRequested(var arguments)
    function begin(){
        if(formId)bridge.activateContentForm(formId,false,priority);
        formId=bridge.newContentFormSession();submission=({});candidates=[];errorText="";conflictDetached=false;
        if(active){bridge.activateContentForm(formId,true,priority);if(operation)bridge.listContentSubmissions(operation,JSON.stringify(target),formId)}
    }
    function end(){if(formId)bridge.activateContentForm(formId,false,priority);formId="";submission=({});candidates=[]}
    function problemText(){return (submission.problems||[]).map(function(p){return ((p.path&&p.path.length?p.path:p.model_location||[]).length?(p.path&&p.path.length?p.path:p.model_location).join(".")+"：":"")+(p.message||p.code||"校验问题")}).join("\n")}
    function releasePending(){
        [owner,controller].forEach(function(item){if(!item)return;["pending","saving","generating","busy","pendingStart","phase2Pending","pendingConversation","timelineBusy","structureBusy","promptSaving"].forEach(function(key){if(typeof item[key]==="boolean"){try{item[key]=false}catch(e){}}})});
        if(typeof owner.restoreEdit==="function")owner.restoreEdit();
        if(typeof owner.errorText==="string")owner.errorText=problemText();
    }
    function focusProblem(problem){
        var path=problem.path&&problem.path.length?problem.path:problem.model_location||[],key=String(path[path.length-1]||"").replace(/_/g,"").toLowerCase();if(!key)return;
        function find(item){if(!item)return null;var name=String(item.objectName||"").replace(/_/g,"").toLowerCase();if(name.indexOf(key)>=0&&typeof item.readOnly==="boolean"&&item.visible)return item;var children=item.children||[];for(var i=0;i<children.length;i++){var value=find(children[i]);if(value)return value}return null}
        var field=find(owner.contentItem||owner);if(field)field.forceActiveFocus();
    }
    function restore(index){var row=candidates[index];if(row)bridge.readContentSubmission(row.submission_id,formId)}
    onActiveChanged:if(active)begin();else end()
    onTargetKeyChanged:if(active&&!restoring)begin()
    onIdentityKeyChanged:if(active&&!restoring)begin()
    onProjectPathChanged:if(active)begin();else end()
    Component.onCompleted:if(active&&!formId)begin()
    property Connections replies:Connections{
        target:bridge
        function onResultReady(kind,payload){
            if(payload.project_path&&payload.project_path!==state.projectPath)return;
            if(kind==="content_submission_conflict"&&payload.form_id===state.formId){state.submission=({});state.conflictDetached=true;state.errorText="正式内容或授权范围已变化。请重新打开对照最新内容，再明确新提交；旧暂存 "+(payload.submission_id||"")+" 仍保留，可按编号查询。\n"+payload.message;state.releasePending();return}
            if(kind==="content_submission"&&payload.form_id===state.formId){state.submission=payload;state.releasePending();return}
            if(kind==="content_submissions"&&payload.form_id===state.formId){state.candidates=(payload.submissions||[]).filter(function(row){return row.status==="staged"&&(!state.allowedActions.length||state.allowedActions.indexOf(row.action_kind)>=0)&&(!state.authoredFields.length||(row.action_fields||[]).some(function(k){return state.authoredFields.indexOf(k)>=0}))});return}
            if(kind==="content_submission_read"&&payload.form_id===state.formId){
                var item=(payload.values||[]).find(function(value){return value.path.length===0});if(!item)return;
                state.submission=payload;state.restoring=true;state.restoreRequested(item.value);state.restoring=false;state.releasePending();return;
            }
            if(payload.content_form_id===state.formId){state.submission=({});state.candidates=[];state.errorText="";if(state.closeOnAccepted&&typeof state.owner.close==="function")state.owner.close()}
        }
        function onErrorOccurred(kind,message){if(state.active&&state.submission.submission_id)state.errorText=message}
    }
}

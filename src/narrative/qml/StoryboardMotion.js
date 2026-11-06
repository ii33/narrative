.pragma library

function pose(object, time) {
    var result=Object.assign({},object)
    delete result.keyframes
    var keys=(object.keyframes||[]).slice().sort((a,b)=>a.time_ms-b.time_ms), left=null, right=null
    keys.forEach(function(key){if(key.time_ms<=time)left=key;else if(!right)right=key})
    if(left){
        ["x","y","rotation","depth","state"].forEach(function(k){if(left[k]!==undefined)result[k]=left[k]})
        if(right&&left.interpolation==="linear"){
            var ratio=(time-left.time_ms)/(right.time_ms-left.time_ms)
            ;["x","y","rotation","depth"].forEach(function(k){result[k]+=(right[k]-result[k])*ratio})
        }
    }
    return result
}

function objects(rows,time) {
    var byId={}, done={}
    rows.forEach(function(o){byId[o.id]=pose(o,time)})
    function world(id){
        if(done[id])return done[id]
        var p=byId[id]
        if(!p)return null
        var q=Object.assign({elevation:0,height:0.2},p)
        done[id]=q
        var parent=world(p.parent_id)
        if(parent){
            var r=parent.world_rotation*Math.PI/180
            q.world_x=parent.world_x+p.x*Math.cos(r)-p.y*Math.sin(r)
            q.world_y=parent.world_y+p.x*Math.sin(r)+p.y*Math.cos(r)
            q.world_rotation=parent.world_rotation+p.rotation
            q.world_depth=parent.world_depth+p.depth
            q.world_elevation=(parent.world_elevation||0)+(p.elevation||0)
        }else{
            q.world_x=p.x;q.world_y=p.y;q.world_rotation=p.rotation
            q.world_depth=p.depth;q.world_elevation=p.elevation||0
        }
        return q
    }
    return rows.map(o=>world(o.id))
}

function ease(ratio,kind){
    if(kind==="ease_in")return ratio*ratio
    if(kind==="ease_out")return 1-(1-ratio)*(1-ratio)
    if(kind==="ease_in_out")return ratio<0.5?2*ratio*ratio:1-2*(1-ratio)*(1-ratio)
    return ratio
}

function cameraPose(shot,local){
    var defaults={x:0.5,y:-1,z:0.4,yaw:0,pitch:0,fov_deg:50,focus_distance:1}
    var result=Object.assign({},defaults), camera=shot.camera||{}
    var keys=(camera.keys||[]).slice().sort((a,b)=>a.offset_ms-b.offset_ms), left=null, right=null
    keys.forEach(function(key){if(key.offset_ms<=local)left=key;else if(!right)right=key})
    if(!left)return result
    Object.keys(defaults).forEach(k=>result[k]=left[k]===undefined?defaults[k]:left[k])
    if(right&&left.interpolation==="linear"){
        var ratio=ease((local-left.offset_ms)/(right.offset_ms-left.offset_ms),left.easing)
        Object.keys(defaults).forEach(function(k){result[k]+=((right[k]===undefined?defaults[k]:right[k])-(left[k]===undefined?defaults[k]:left[k]))*ratio})
        var center=camera.orbit_center
        if(camera.preset==="orbit"&&center){
            var lx=left.x-center.x, ly=left.y-center.y, rx=right.x-center.x, ry=right.y-center.y
            var angle=Math.atan2(ly,lx)
            var delta=((Math.atan2(ry,rx)-angle+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI
            var radius=Math.hypot(lx,ly)+(Math.hypot(rx,ry)-Math.hypot(lx,ly))*ratio
            result.x=center.x+radius*Math.cos(angle+delta*ratio)
            result.y=center.y+radius*Math.sin(angle+delta*ratio)
        }
    }
    return result
}

function cameraView(board,shot,local,states){
    var camera=shot.camera||{}, p=cameraPose(shot,local)
    var target=states.find(o=>o.id===camera.target_object_id)
    if(target){
        if(camera.preset==="follow"||camera.preset==="orbit"){
            var base=objects(board.objects||[],(shot.action_start_ms||0)+(camera.start_ms||0)).find(o=>o.id===target.id)
            p.x+=target.world_x-base.world_x;p.y+=target.world_y-base.world_y
            p.z+=(target.world_elevation||0)-(base.world_elevation||0)
        }
        if(["follow","orbit","reveal","dolly_zoom"].includes(camera.preset)){
            var dx=target.world_x-p.x, dy=target.world_y-p.y
            var dz=(target.world_elevation||0)+(target.height||0.2)/2-p.z
            p.yaw=Math.atan2(dx,dy)*180/Math.PI
            p.pitch=Math.atan2(dz,Math.hypot(dx,dy))*180/Math.PI
        }
        if(camera.preset==="dolly_zoom"&&camera.subject_size_ratio){
            var distance=Math.hypot(target.world_x-p.x,target.world_y-p.y,(target.world_elevation||0)+(target.height||0.2)/2-p.z)
            p.fov_deg=2*Math.atan((target.height||0.2)/(2*distance*camera.subject_size_ratio))*180/Math.PI
        }
    }
    if(camera.handheld){
        var h=camera.handheld, a=h.amplitude, u=2*Math.PI*h.frequency_hz*local/1000, phase=(h.seed%10007)*0.61803398875
        p.x+=a*Math.sin(u+phase);p.y+=a*0.5*Math.cos(u*1.31+phase*0.7)
        p.z+=a*0.5*Math.sin(u*0.79+phase*1.3)
        p.yaw+=a*20*Math.sin(u*0.67+phase*1.7);p.pitch+=a*12*Math.cos(u*0.83+phase*1.1)
    }
    var focus=states.find(o=>o.id===camera.focus_target_object_id)
    if(focus)p.focus_distance=Math.max(0.000001,Math.hypot(focus.world_x-p.x,focus.world_y-p.y,(focus.world_elevation||0)+(focus.height||0.2)/2-p.z))
    var yaw=p.yaw*Math.PI/180, pitch=p.pitch*Math.PI/180
    var right=[Math.cos(yaw),-Math.sin(yaw),0]
    var forward=[Math.sin(yaw)*Math.cos(pitch),Math.cos(yaw)*Math.cos(pitch),Math.sin(pitch)]
    var up=[-Math.sin(yaw)*Math.sin(pitch),-Math.cos(yaw)*Math.sin(pitch),Math.cos(pitch)]
    var tangent=Math.tan(p.fov_deg*Math.PI/360)
    function dot(a,b){return a.reduce((sum,v,i)=>sum+v*b[i],0)}
    var projections=states.map(function(o){
        var delta=[o.world_x-p.x,o.world_y-p.y,(o.world_elevation||0)+(o.height||0.2)/2-p.z]
        var depth=dot(delta,forward), front=depth>0.000001
        var x=front?0.5+dot(delta,right)/(2*depth*tangent*16/9):null
        var y=front?0.5-dot(delta,up)/(2*depth*tangent):null
        var visible=front&&x>=0&&x<=1&&y>=0&&y<=1
        return{object_id:o.id,screen_x:x,screen_y:y,size_ratio:front?(o.height||0.2)/(2*depth*tangent):null,depth:depth,visible:visible,occluded:false,in_focus:visible&&Math.abs(Math.hypot(delta[0],delta[1],delta[2])-p.focus_distance)<=Math.max(0.01,p.focus_distance*0.1)}
    })
    projections.forEach(function(o){if(o.visible)o.occluded=projections.some(other=>other.visible&&other.depth<o.depth&&Math.abs(other.screen_x-o.screen_x)<other.size_ratio/4&&Math.abs(other.screen_y-o.screen_y)<other.size_ratio/2)})
    return{offset_ms:local,action_ms:(shot.action_start_ms||0)+local,position:{x:p.x,y:p.y,z:p.z},yaw:p.yaw,pitch:p.pitch,fov_deg:p.fov_deg,focus_distance:p.focus_distance,focus_model:"2.5d_distance_band_not_optical_dof",projections:projections,frustum:{near:0.000001,vertical_fov_deg:p.fov_deg,aspect_ratio:16/9,forward:forward},projection_model:"2.5d_no_mesh_approximate_occlusion"}
}

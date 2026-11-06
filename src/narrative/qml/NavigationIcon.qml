import QtQuick

Canvas {
    id:icon
    objectName:"navigationIcon_"+name
    property string name:"browse"
    property color ink:"#293746"
    implicitWidth:24;implicitHeight:24
    onNameChanged:requestPaint()
    onInkChanged:requestPaint()
    onPaint:{
        var c=getContext("2d");c.reset();c.clearRect(0,0,width,height);c.strokeStyle=ink;c.fillStyle=ink;c.lineWidth=1.7;c.lineCap="round";c.lineJoin="round"
        function line(x1,y1,x2,y2){c.beginPath();c.moveTo(x1,y1);c.lineTo(x2,y2);c.stroke()}
        function rect(x,y,w,h){c.strokeRect(x,y,w,h)}
        function circle(x,y,r){c.beginPath();c.arc(x,y,r,0,Math.PI*2);c.stroke()}
        if(name==="browse"){rect(3,3,7,7);rect(14,3,7,7);rect(3,14,7,7);rect(14,14,7,7)}
        else if(name==="relations"){line(7,7,17,7);line(7,7,12,18);line(17,7,12,18);circle(6,6,3);circle(18,6,3);circle(12,19,3)}
        else if(name==="source"){rect(3,4,18,16);line(12,4,12,20);line(6,8,9,8);line(15,8,18,8);line(6,12,9,12);line(15,12,18,12)}
        else if(name==="adaptation"){c.beginPath();c.moveTo(5,15);c.lineTo(16,4);c.lineTo(20,8);c.lineTo(9,19);c.lineTo(4,20);c.closePath();c.stroke();line(14,6,18,10)}
        else if(name==="assets"){rect(3,7,15,14);rect(7,3,14,14);line(9,14,12,10);line(12,10,18,15);circle(16,7,1.4)}
        else if(name==="screenplay"){rect(5,2,14,20);line(8,7,16,7);line(8,11,16,11);line(8,15,14,15);line(8,19,12,19)}
        else if(name==="storyboard"){rect(3,8,18,13);rect(3,3,18,5);line(7,3,4,8);line(13,3,10,8);line(19,3,16,8);line(8,13,16,13);line(8,17,13,17)}
        else if(name==="director"){rect(2,3,20,13);line(12,16,12,21);line(7,21,17,21);circle(8,9,2);line(13,7,19,7);line(13,11,17,11)}
        else if(name==="screenplay_structure"){rect(9,2,6,5);line(12,7,12,12);line(5,12,19,12);line(5,12,5,16);line(19,12,19,16);rect(2,16,6,5);rect(16,16,6,5)}
        else if(name==="static_check"){circle(12,12,9);line(7,12,10,15);line(10,15,17,8)}
        else if(name==="project_settings"){circle(12,12,4);for(var i=0;i<8;i++){var a=i*Math.PI/4;line(12+7*Math.cos(a),12+7*Math.sin(a),12+10*Math.cos(a),12+10*Math.sin(a))}}
        else if(name==="back"){line(4,12,20,12);line(4,12,11,5);line(4,12,11,19)}
    }
}

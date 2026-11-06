import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ColumnLayout{
 id:fields;property var pose:({});property string prefix:"cameraPose"
 property var specs:[{key:"x",label:"横向位置"},{key:"y",label:"纵向位置"},{key:"z",label:"高度"},{key:"yaw",label:"朝向（度）"},{key:"pitch",label:"俯仰（度）"},{key:"fov_deg",label:"视野（度）"},{key:"focus_distance",label:"对焦距离"}]
 function setPose(value){pose=Object.assign({x:0.5,y:-1,z:0.4,yaw:0,pitch:0,fov_deg:50,focus_distance:1},value||{})}
 function current(){var result={};for(var i=0;i<rows.count;i++)result[specs[i].key]=Number(rows.itemAt(i).field.text);return result}
 Repeater{id:rows;model:fields.specs;RowLayout{required property var modelData;property alias field:valueField;Layout.fillWidth:true;Label{text:modelData.label;Layout.preferredWidth:100}TextField{id:valueField;objectName:fields.prefix+modelData.key;text:String(fields.pose[modelData.key]===undefined?0:fields.pose[modelData.key]);validator:DoubleValidator{} Layout.fillWidth:true}}}
}

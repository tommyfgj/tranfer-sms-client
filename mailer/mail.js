var spawn = require('child_process').spawn;
var process = require('process');

var p = spawn('node',['pduDecode.js'],{
	        detached : true
	    });
console.log(process.pid, p.pid);

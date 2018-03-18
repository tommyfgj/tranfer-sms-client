
exports.sendMail = function (mailFrom, mailTo, sender, senderMail, mailBody) {
    var logger = require('tracer').dailyfile({root:'../logs', maxLogFiles: 10, allLogsFileName: 'mailer', level: 'info'});
    var nodemailer = require('nodemailer');
    var mailTitle = new Buffer(mailBody).toString('base64');
    var transporter = nodemailer.createTransport({
        sendmail: true,
        newline: 'unix',
        path: '/usr/sbin/sendmail'
    });
    
    var message = {
        envelope: {
            from: mailFrom,
            to: [mailTo]
        },
        raw: `From: ${sender} <${senderMail}> 
To: <${mailTo}> 
Subject: =?UTF-8?B?${mailTitle}?= 

${mailBody}`
};
    
    transporter.sendMail(message, (err, info) => {
        console.log(err, info)
    });
    logger.info(`mailTo[${mailTo}] sender[${sender}] body[${mailBody}]`);
}

//sendMail("15366190145@crosscn.net", "412816322@qq.com", "15366", "15366190145@crosscn.net", "你刚好");

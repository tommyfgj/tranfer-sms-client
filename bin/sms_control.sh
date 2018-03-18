#!/bin/bash

BIN_DIR=$(cd "$(dirname "$0")"; pwd)
HOME_DIR=${BIN_DIR}/..
CONF_DIR=${HOME_DIR}/conf
LOG_DIR=${HOME_DIR}/logs
PROGRAMME=sms
PID_FILE=${LOG_DIR}/${PROGRAMME}.pid
CONF_FILE=${CONF_DIR}/${PROGRAMME}.conf

function status() {
    start_check
    if [ $? -eq 0 ];then
        echo "$1 ${PROGRAMME}, ${PROGRAMME} is running..."
        return 0
    else
        echo "$1 ${PROGRAMME}, ${PROGRAMME} is not running..."
        return 1
    fi
}

function start_check() {
    if [ ! -f ${PID_FILE} ];then
        return 1
    fi
    pid=`cat ${PID_FILE}`
    ret=`kill -0 $pid > /dev/null`
}

function start() {
    start_check
    if [ $? -eq 0 ];then
        echo "${PROGRAMME} is running, exiting..."
        return 1
    else
        cd ${BIN_DIR} && supervisord -c ../conf/${PROGRAMME}.conf
        #${PROGRAMME} -c ${CONF_FILE}
        sleep 3
        status "start"
    fi
}

function stop() {
    start_check
    if [ $? -eq 1 ];then
        echo "${PROGRAMME} is not running..."
        return 0
    else
        if [ ! -f ${PID_FILE} ];then
            echo "${PROGRAMME} pid file is not exist, exiting..."
            return 1
        fi
        pid=`cat ${PID_FILE}`
        kill -15 $pid
        sleep 1
        status "stop"
    fi

}

function restart() {
    stop
    sleep 1
    start
}

case "${1}" in
start)
    start
    ;;
stop)
    stop
    ;;
restart)
    restart
    ;;
status)
    status "check"
    ;;
*)
    help
    ;;
esac

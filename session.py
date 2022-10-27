from audioop import getsample
import collections
import os
from datetime import date
from modulefinder import replacePackageMap
from time import perf_counter, sleep

import matplotlib.pyplot as plt
import mysql.connector
import numpy as np
from numpy.random import RandomState
import pandas as pd
import pygame
from sympy import evaluate
import RPi.GPIO as GPIO

import hardware.mice_ui as mice_ui
from analysis.benchmark.benchmark import benchmark
from analysis.benchmark.evaluate import get_performance_new, get_switches
from analysis.monitor import monitor_train
from database import queries
from database.queries import upload_session
from hardware.mice_ui import Block_UI
from hardware.pump_ctrl import Pump
from hardware.setup import setup

# TODO sort out the variables
# variables storing trial data
MOTOR_REWARD = 0.9
reward_prob = np.array([0.9, 0.9])
choices = []
leftP = []
rightP = []
trial_indices = []
rewarded = []
reaction_time = []
moving_speed = []

# choice made in a trial, left is 0 and right is 1
choice = 0
# advantageous side, 1 is right, 0 is left
adv = 0
# trial index
trial_ind = 0
# number of movement in trial interval, used to determine whether the trial should start
movement = 0
# number of movement during a trial
trial_movement = 0

# Control a mice session with loaded probability file
IN_A = 4
IN_B = 5
OUT_REWARD = 6

# seconds before a trial times out and we get a NaN trial
TIME_OUT = 7
# Flag indicating whether we are in the middle of a trial or not
in_trial = False
# enure only one choice is stored
choice_made = False
TRIAL_NUM = {'motor_training': 300, 'training_1': 400, 'training_1_1': 400, 'training_1_2': 400,
             'training_2': 450, 'training_2_1': 450, 'training_2_1': 450}

prob_set = -3

Encoder_A = 0
Encoder_A_old = 0
Encoder_B = 0
Encoder_B_old = 0

# probability limits
MOTOR = 0.85

GPIO.setmode(GPIO.BCM)
# Setup input and output pins with GPIO
GPIO.setup(IN_A, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(IN_B, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(OUT_REWARD, GPIO.OUT, initial=GPIO.LOW)

fig, axes = plt.subplots(1, 1, figsize=(16, 8))
axes.set_ylim([-1.3, 1.3])

today = date.today()
today.strftime('YYYY/MM/dd')

# Creating connection
config = {
    'host': 'dynamic-foraging-mysql.mysql.database.azure.com',
    'user': 'peiheng',
    'password': 'Luph65588590-',
    'database': 'dynamic-foraging'
}


try:
    db = mysql.connector.connect(**config)
    cursor = db.cursor()
except Exception:
    print('connection error')


mice = queries.get_animals(cursor)
pump = Pump(OUT_REWARD)
mouse_code = setup(pump, mice)
mode = queries.get_stage(mouse_code, cursor)

session_length_offset = queries.get_offset(mouse_code)
queries.set_offset(mouse_code, cursor, session_length_offset)
session_length = TRIAL_NUM[mode] + session_length_offset

block = Block_UI(mode)
pygame.mixer.init()
beep = pygame.mixer.Sound('beep.mp3')


def quadrature_decode(callback):
    global Encoder_A
    global Encoder_A_old
    global Encoder_B
    global Encoder_B_old
    global in_trial
    global movement
    global choice_made
    global trial_movement
    global block
    global choice

    Encoder_A = GPIO.input(IN_A)
    Encoder_B = GPIO.input(IN_B)

    if (Encoder_A == 1 and Encoder_B_old == 0) or (Encoder_A == 0 and Encoder_B_old == 1):
        # this will be clockwise rotation
        if in_trial:
            if not choice_made:
                trial_movement += 1
                if block.update_right(in_trial):
                    #in_trial = False
                    print('chose right')
                    # chose right
                    choice = 1
                    choice_made = True
        else:  # trial interval
            movement += 1

    elif (Encoder_A == 1 and Encoder_B_old == 1) or (Encoder_A == 0 and Encoder_B_old == 0):
        # this will be counter-clockwise rotation
        if in_trial:
            if not choice_made:
                trial_movement += 1
                if block.update_left(in_trial):
                    #in_trial = False
                    print('chose left')
                    # chose left
                    choice = 0
                    choice_made = True
        else:  # trial interval
            movement += 1

    Encoder_A_old = Encoder_A
    Encoder_B_old = Encoder_B


GPIO.add_event_detect(IN_A, GPIO.BOTH, callback=quadrature_decode)
GPIO.add_event_detect(IN_B, GPIO.BOTH, callback=quadrature_decode)


if mode == 'motor_training':
    print('motor training')
    # reward prob for motor training
    reward_prob[0] = MOTOR_REWARD
    reward_prob[1] = MOTOR_REWARD
    prob_set = -3
elif 'training_1' in mode:
    adv = np.random.binomial(1, 0.5)
    reward_prob[adv] = np.random.uniform(low=0.9, high=0.95, size=1)
    reward_prob[abs(1-adv)] = 1 - reward_prob[adv]
    prob_set = -2
elif 'training_2' in mode:
    adv = np.random.binomial(1, 0.5)
    reward_prob[adv] = np.random.uniform(low=0.85, high=0.9, size=1)
    reward_prob[abs(1-adv)] = 1 - reward_prob[adv]
    prob_set = -1
else:
    prob_set = int(mode)

rs = RandomState()

if prob_set >= 0:
    rs.seed(prob_set)
    reward_prob[adv] = rs.uniform(low=0.85, high=0.9, size=1)
    reward_prob[abs(1-adv)] = 1 - reward_prob[adv]

session_start_time = perf_counter()

# number of trials spend on the current block
curr_block = 0
# percentage of the animal choosing the more advantagous side in the past twenty trials
last_twenty = collections.deque(20*[0], 20)

# trial continues until stopped
while session_length > 0 and perf_counter() - session_start_time < 2700:
    print(reward_prob)
    # stop the system bring up not responding window
    pygame.event.pump()
    # random trial interval
    sleep(np.random.randint(0, 1))
    if prob_set >= 0:
        block_length = rs.randint(low=50, high=80)
    else:
        block_length = np.random.randint(low=50, high=80)

    # block switch in trianing mode
    if prob_set > -3 and curr_block >= block_length and (curr_block-60) % 20 == 0 and last_twenty.count(1) > 15:
        print('switch prob')
        curr_block = 0
        adv = abs(1 - adv)
        if prob_set == -2:
            reward_prob[adv] = np.random.uniform(low=0.9, high=0.95)
        elif prob_set == -1:
            reward_prob[adv] = np.random.uniform(low=0.8, high=0.85)
        else:
            reward_prob[adv] = rs.uniform(low=0.8, high=0.85)
        reward_prob[abs(1-adv)] = 1 - reward_prob[adv]

    # next trial doesn't start until the animal stop moving the wheel for 0.5s
    while True:
        print('stuck here')
        movement = 0
        sleep(0.5)
        if movement < 10:
            break

    beep.play()
    sleep(0.1)
    beep.stop()

    start_time = perf_counter()
    choice = -1
    choice_made = False
    trial_movement = 0
    print('trial starts')
    block.draw()
    in_trial = True
    # Trial continues until the mouse made a choice or timeout
    while in_trial:
        if choice != -1:
            print('choice made')
            reaction_time.append(perf_counter() - start_time)
            moving_speed.append(trial_movement / (perf_counter() - start_time))
            if np.random.binomial(1, reward_prob[choice]):
                # if given reward
                pump.send_reward(mode)
                rewarded.append(1)
                print('rewarded')
            else:
                rewarded.append(0)
            # chosen the advantageous side
            last_twenty.append(int(adv == choice))
            in_trial = False
            break
        block.draw()
        if perf_counter() - start_time > TIME_OUT:
            # no reward for nan trials either
            rewarded.append(0)
            reaction_time.append(-1)
            moving_speed.append(-1)
            last_twenty.append(0)
            in_trial = False
            break
    block.reset()
    block.window.fill(mice_ui.BG_COLOR)
    pygame.display.flip()
    # store trial data
    trial_indices.append(trial_ind)
    leftP.append(reward_prob[0])
    rightP.append(reward_prob[1])
    choices.append(choice)
    # next trial
    trial_ind += 1
    curr_block += 1
    print(trial_ind)

    if prob_set > -3:
        monitor_train(left_p=leftP, axes=axes, trial_indices=trial_indices,
                      choices=choices, rewarded=rewarded)
        plt.show(block=False)
        plt.pause(0.1)
    else:
        print(get_performance_new(np.array(choices), np.array(leftP), mode))

    session_length -= 1

    pygame.mixer.quit()
    pygame.mixer.init(buffer=4096)
    beep = pygame.mixer.Sound('beep.mp3')

perf = get_performance_new(choices=choices, leftP=leftP, mode=mode)
session_time = (perf_counter() - session_start_time) / 60
nan_percent = float(np.sum([choice == -1 for choice in choices])) / float(session_length)

if prob_set < 0:
    passed = benchmark(stage=mode, choices=np.array(
        choices), leftP=np.array(leftP))

    if passed:
        queries.next_stage(mouse_code, cursor=cursor, stage=mode)
    else:
        queries.backtrack(mouse_code, cursor=cursor, stage=mode)
    
    if prob_set > -3:
        if session_time < 40 and nan_percent < 0.05:
            session_length_offset += 20
        elif session_time > 45 and nan_percent > 0.1:
            session_length_offset -= 20
        queries.set_offset(mouse_code, cursor, session_length_offset)

if prob_set >= 0:
    queries.next_set(mouse_code, prob_set, cursor)

queries.upload_session(mouse_code, today, stage=mode, prob_set=prob_set, choices=choices, rewarded=rewarded,
                       trial_indices=trial_indices, leftP=leftP, rightP=rightP, reaction_time=reaction_time, moving_speed=moving_speed, cursor=cursor, performance=perf, session_time=session_time, nan_percent=nan_percent)

db.commit()

print('session time: %f' % (session_time))
print(f'switches: {get_switches(leftP)}')

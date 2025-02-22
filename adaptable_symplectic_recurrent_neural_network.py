# -*- coding: utf-8 -*-
"""Adaptable Symplectic Recurrent Neural Network.py

"""

import numpy as np
import matplotlib.pyplot as plt
from tqdm import trange
import torch
import torch.nn as nn
from torch import optim
import torch.nn.functional as F
from mpl_toolkits import mplot3d

from tqdm import tqdm

if torch.cuda.is_available():
    dev = "cuda:0"
else:
    dev = "cpu"

device = torch.device(dev)

##### The below class defines an Adaptable HNN of the architecture proposed by Han et al.######

class adaptable_HNN(nn.Module):
    def __init__(self, input_size, num_hidden, num_neurons, n_params):
      super(adaptable_HNN, self).__init__()
      self.input_size = input_size # dimensionality of the system
      self.num_hidden = num_hidden #No. of hidden layers in the HNN
      self.num_neurons = num_neurons #A list containing number of neurons per layer for hidden layers(should be of length num_hidden)
      self.n_params = n_params #no. of parameters

      #defining a sequential model that takes in q, p and params and gives H(q, p; params)
      self.input_model = nn.Sequential(nn.Linear(input_size+n_params, num_neurons[0]), nn.Tanh()) 
      for i in range(num_hidden-1):
        self.input_model.append(nn.Linear(num_neurons[i], num_neurons[i+1]))
        self.input_model.append(nn.Tanh())
    
      self.energy_layer = nn.Sequential(nn.Linear(num_neurons[-1], 1))


    def forward(self, q, p, params, training=True):
      self.input = torch.cat((q, p, params), dim=1)
      sequential_output = self.input_model(self.input)
      H = self.energy_layer(sequential_output)
      if training==True: #defines whether the network is in training or predicting
        dH_dq, dH_dp = torch.autograd.grad(H.sum(), (q, p), create_graph=True) #returns delH_delq, delH_delp and enforces Hamilton's equations by comparing them to -pdot and qdot
      else:
        dH_dq, dH_dp = torch.autograd.grad(H.sum(), (q, p))

      return H, -dH_dq, dH_dp


##### The below class defines an Adaptable Partial HNN of the architecture proposed by Han et al. except only for separable Hamiltonians where K=K(p)
##### and V = V(q; params). H = K(p) + V(q; params) ######

class adaptable_partial_HNN(nn.Module): #Net for Kinetic Energy or Potential Energy, depending on the 'potential' flag
  def __init__(self, input_size, num_hidden, num_neurons, n_params, potential=False):
    super(adaptable_partial_HNN, self).__init__()
    self.input_size = input_size #half the dimensionality of the system(no. of position variables)
    self.num_hidden = num_hidden #No. of hidden layers in the HNN
    self.num_neurons = num_neurons #A list containing number of neurons per layer for hidden layers(should be of length num_hidden)
    self.n_params = n_params #no. of parameters (Considering separable hamiltonians it is assumed only the potential is a function of these parameters and K = K(q) only)
    self.potential = potential #potential flag

    if self.potential: #defining a sequential model that takes in q and params and gives V(q; params)
      self.input_model = nn.Sequential(nn.Linear(input_size+n_params, num_neurons[0]), nn.Tanh())
      for i in range(num_hidden-1):
        self.input_model.append(nn.Linear(num_neurons[i], num_neurons[i+1]))
        self.input_model.append(nn.Tanh())
    
      self.energy_layer = nn.Sequential(nn.Linear(num_neurons[-1], 1))

    else: #defining a sequential model that takes in p and returns K(p)
      self.input_model = nn.Sequential(nn.Linear(input_size, num_neurons[0]), nn.Tanh())
      for i in range(num_hidden-1):
        self.input_model.append(nn.Linear(num_neurons[i], num_neurons[i+1]))
        self.input_model.append(nn.Tanh())
    
      self.energy_layer = nn.Sequential(nn.Linear(num_neurons[-1], 1))


  def forward(self, qp, params, training=True):
    if self.potential:
      self.input = torch.cat((qp, params), dim=1)
      sequential_output = self.input_model(self.input)
      V = self.energy_layer(sequential_output)
      if training==True: #defines whether the network is in training or predicting
        dH_dq = torch.autograd.grad(V.sum(), qp, create_graph=True) #gradient of V(q; params) w.r.t q ---> dp/dt
        return V, -dH_dq[0]
      else:
        dH_dq = torch.autograd.grad(V.sum(), qp) 
        return V, -dH_dq[0]

    else:
      sequential_output = self.input_model(qp.clone())
      K = self.energy_layer(sequential_output)
      if training == True:
        dH_dp = torch.autograd.grad(K.sum(), qp, create_graph=True) #gradient of K(p) w.r.t p ---> dq/dt
        return K, dH_dp[0]
      else:
        dH_dp = torch.autograd.grad(K.sum(), qp)
        return K, dH_dp[0]

##### The below class creates a class modelled on the architecture proposed by Chen et al. but made adaptable by adding parameter channels in the manner proposed by Han et al. #####

class adaptable_sympRNN(nn.Module):

  def __init__(self, input_size, num_hidden, num_neurons, n_params, dt, separable=True):
    super(adaptable_sympRNN, self).__init__()
    self.input_size = input_size 
    self.num_hidden = num_hidden
    self.num_neurons = num_neurons
    self.n_params = n_params 
    self.separable = separable #flag that assumes separable Hamiltonian or not
    
    self.dt = dt #the time separation between subsequent steps of the Recurrent units

    if self.separable==True:
      self.K_net = adaptable_partial_HNN(int(self.input_size/2), self.num_hidden, self.num_neurons, self.n_params, potential=False)
      self.V_net = adaptable_partial_HNN(int(self.input_size/2), self.num_hidden, self.num_neurons, self.n_params, potential=True)
    else:
      self.HNN = adaptable_HNN(self.input_size, self.num_hidden, self.num_neurons, self.n_params)

  def forward(self, q_initial, p_initial, params, final, training=True):
    batch, dim = q_initial.shape
    q, p = torch.zeros(final+1, batch, dim).to(device), torch.zeros(final+1, batch, dim).to(device)
    H = torch.zeros(final, batch, 1).to(device)
    q[0] = q_initial ; p[0] = p_initial
    for t in range(final):
      if self.separable==True:
        H[t], q[t+1], p[t+1] = self.step(q[t], p[t], params, training)
      else:
        H[t], q[t+1], p[t+1] = self.step2(q[t], p[t], params, training)

    #H[-1], _, __ = self.HNN(q[-1], p[-1], params, training)
    return H, q[1:], p[1:]

  def step(self, q, p, params, training):
    if training==True:
      #implementation of leapfrog(symplectic algorithm)
      K, qdot = self.K_net(p, params)
      q_half = q + (self.dt)/2 * qdot
      V, pdot_t = self.V_net(q_half, params)
      p_next = p + self.dt * pdot_t
      K, qdot_t = self.K_net(p_next, params)
      q_next = q_half + (self.dt)/2 * qdot_t
      V, pdot = self.V_net(q_next, params)
    else:
      K, qdot = self.K_net(p, params, training=False)
      q_half = q + (self.dt)/2 * qdot
      V, pdot = self.V_net(q_half, params, training=False)
      p_next = p + self.dt * pdot
      K, qdot = self.K_net(p_next, params, training=False)
      q_next = q_half + (self.dt)/2 * qdot
      V, pdot = self.V_net(q_next, params, training=False)
 
    return K+V, q_next, p_next

  def step2(self, q, p, params, training):
    if training==True:
      H, pdot, qdot = self.HNN(q, p, params)
      q_half = q + (self.dt)/2 * qdot
      _, pdot_t, qdot_t = self.HNN(q_half, p, params)
      p_next = p + self.dt * pdot_t
      _, pdot_half, qdot_t = self.HNN(q_half, p_next, params)
      q_next = q_half + (self.dt)/2 * qdot_t
    else:
      H, pdot, qdot = self.HNN(q, p, params, training=False)
      q_half = q + (self.dt)/2 * qdot
      _, pdot_t, qdot_t = self.HNN(q_half, p, params, training=False)
      p_next = p + self.dt * pdot_t
      _, pdot_half, qdot_t = self.HNN(q_half, p_next, params, training=False)
      q_next = q_half + (self.dt)/2 * qdot_t
 
    return H, q_next, p_next
  

  def call_V(self, q, params):
    return self.V_net(q, params, training=False)

  def call_K(self, p):
    return self.K_net(p, None, training=False)

  def call_HNN(self, q, p, params):
    return self.HNN(q, p, params, training=False)

#the below function takes in a adaptive symplectic recurrent neural network and performs simultaneous training and validation
def train_validate(model, q_input_train, p_input_train, params_train, q_output_train, p_output_train,
                              q_input_valid, p_input_valid, params_valid, q_output_valid, p_output_valid, 
                              n_epochs, batch_size, learning_rate=0.001): 
  
  optimizer = optim.Adam(model.parameters(), lr=learning_rate)
  criterion = nn.MSELoss() #Mean Square error loss

  num_train = int(q_input_train.shape[0]/batch_size)
  num_valid = int(q_input_valid.shape[0]/batch_size)

  time = q_output_train.shape[0] #how far in time to predict

  training_loss = np.full(n_epochs, np.nan)
  validation_loss = np.full(n_epochs, np.nan)

  with trange(n_epochs) as tr:
    #training
    for it in tr:

      train_batch_loss = 0
      model.train() #putting model in training mode
      for b in range(num_train):
        #we take slices of the data of size batch_size
        q_input_train_batch = q_input_train[b:b+batch_size, :] 
        p_input_train_batch = p_input_train[b:b+batch_size, :]
        q_output_train_batch = q_output_train[:, b:b+batch_size, :]
        p_output_train_batch = p_output_train[:, b:b+batch_size, :]

        params_train_batch = params_train[b:b+batch_size, :]

        optimizer.zero_grad()
        _, q, p = model(q_input_train_batch, p_input_train_batch, params_train_batch, time) #calling model
        
        train_loss = criterion(torch.cat((q, p), dim=2),
                               torch.cat((q_output_train_batch, p_output_train_batch), dim=2))
        
        train_batch_loss += train_loss.item()
        train_loss.backward()
        optimizer.step()

      train_batch_loss /= num_train
      training_loss[it] = train_batch_loss

      #validate
      valid_batch_loss = 0
      model.eval()
      for b in range(num_valid):
        q_input_valid_batch = q_input_valid[b:b+batch_size, :]
        p_input_valid_batch = p_input_valid[b:b+batch_size, :]
        q_output_valid_batch = q_output_valid[:, b:b+batch_size, :]
        p_output_valid_batch = p_output_valid[:, b:b+batch_size, :]

        params_valid_batch = params_valid[b:b+batch_size, :]

        optimizer.zero_grad()
        _, q, p = model(q_input_valid_batch, p_input_valid_batch, params_valid_batch, time)
        #pdot, qdot = calc_grad(H, qtrain_batch, ptrain_batch)
        valid_loss = criterion(torch.cat((q, p), dim=2),
                               torch.cat((q_output_valid_batch, p_output_valid_batch), dim=2))

        valid_batch_loss += valid_loss.item()

      valid_batch_loss /= num_valid
      validation_loss[it] = valid_batch_loss

  return training_loss, validation_loss

import os

import torch
from tokenizers.implementations import BertWordPieceTokenizer
from torch import nn

import torch.nn.functional as F
from transformers import AutoTokenizer

from datasetOld import dump_load, dump_write, NUS
from torch.utils.data import DataLoader

class encoderAbsolute(nn.Module):
    # Create neural network
    def __init__(self,embedding_dim, hidden_dim,number_layers=2,drop_out=0.1):
        super(encoderAbsolute, self).__init__()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained('sentence-transformers/paraphrase-distilroberta-base-v1')
        self.embedding_dim = embedding_dim
        self.hidden_dim = hidden_dim
        self.word_embeds = nn.Embedding(self.tokenizer.vocab_size, embedding_dim).to(self.device)
        nn.init.xavier_uniform_(self.word_embeds.weight)
        self.forwardLSTM = nn.ModuleList().to(self.device)
        self.backwardLSTM = nn.ModuleList().to(self.device)
        for i in range(number_layers):
            input_size = embedding_dim if i == 0 else hidden_dim
            self.forwardLSTM.append(nn.LSTM(input_size, hidden_dim, num_layers=1)).to(self.device)
            self.backwardLSTM.append(nn.LSTM(input_size, hidden_dim, num_layers=1)).to(self.device)

        self.positionEmbeddings = nn.Embedding(200,embedding_dim).to(self.device)
        self.predicateEmbeddings = nn.Embedding(2,embedding_dim).to(self.device)
        self.verschil = nn.Embedding(86,embedding_dim).to(self.device)
        self.dropout = nn.Sequential(
            torch.nn.Dropout(p=drop_out),
            torch.nn.ReLU(),
        ).to(self.device)
        self.number_layers = number_layers
        self.skip= nn.Sequential(
            nn.Identity(),
            torch.nn.Dropout(p=drop_out),
            torch.nn.ReLU(),
        ).to(self.device)
        self.batch = 0


    def forward(self, claim,date,positions,verbs,times,verschillenIndices,verschillenValues,train=False,isClaim = True):
        encoded_input = self.tokenizer(claim, padding=True, truncation=False, return_tensors='pt').to(self.device)
        #tokens = [[i for i in self.tokenizer(claim)['input_ids'] if i not in {0,2,4}]]
        #tokens = torch.tensor(tokens).to(self.device)
        inputForward = self.word_embeds(encoded_input['input_ids']).to(self.device)
        alpha = 0.90
        if positions[0]!="":
            for position in positions:
                position = position.split(',')
                inputForward[0][int(position[0])] = alpha*inputForward[0][int(position[0])] + (1-alpha)*self.positionEmbeddings(torch.tensor([int(position[1])+100]).to(self.device)).squeeze(0).to(self.device)
        if verbs[0]!="":
            for verb in verbs:
                inputForward[0][int(verb)] = alpha*inputForward[0][int(verb)] + (1-alpha)*self.predicateEmbeddings(torch.tensor([0]).to(self.device)).squeeze(0).to(self.device)
        if times[0]!="":
            for time in times:
                inputForward[0][int(time)] = alpha*inputForward[0][int(time)] + (1-alpha)*self.predicateEmbeddings(torch.tensor([1]).to(self.device)).squeeze(0).to(self.device)
        if verschillenIndices[0]!="":
            for i in range(len(verschillenIndices)):
                index = verschillenIndices[i]
                if verschillenValues[i].find('Duur')==-1 and verschillenValues[i].find('Refs')==-1:
                    if verschillenValues[i].isdigit():
                        inputForward[0][int(index)] = alpha*inputForward[0][int(index)] + (1-alpha)* self.verschil(torch.tensor([int(verschillenValues[i])]).to(self.device)).squeeze(0).to(self.device)
                if i+1 >= len(verschillenValues):
                    break
        inputForward = torch.nn.functional.normalize(inputForward,p=2.0)
        inputForward = self.dropout(inputForward)
        inputBackward = torch.flip(inputForward,[1]).to(self.device)
        outputForwards = torch.tensor([]).to(self.device)
        outputBackWards = torch.tensor([]).to(self.device)
        for i in range(self.number_layers):
            if i != 0:
                inputForward = self.dropout(inputForward)
                inputBackward = self.dropout(inputBackward)
                #skip connections
                for j in range(i):
                    inputForward = inputForward + self.skip(outputForwards[j])
                    inputBackward = inputBackward + self.skip(outputBackWards[j])

            outputForward, hiddenForward = self.forwardLSTM[i](inputForward)
            outputBackWard, hiddenBackward = self.backwardLSTM[i](inputBackward)
            outputForwards = torch.cat((outputForwards,outputForward))
            outputBackWards = torch.cat((outputBackWards,outputBackWard))
            inputForward = outputForward
            inputBackward = outputBackWard
        return torch.cat((self.dropout(outputForward[0][-1]), self.dropout(outputBackWard[0][-1])))

    '''
    Function for saving the neural network
    '''
    def saving_NeuralNetwork(self, path):
        torch.save(self.state_dict(), path)

    '''
    Function for loading the configurations from a file
    It first reads the configurations of a file
    Then it initialises a neural network with the parameters of the file
    Then it sets the neural network on the state of the loaded neural network
    '''
    def loading_NeuralNetwork(self,path):
        self.load_state_dict(torch.load(path))
        self.eval()


if __name__ == "__main__":

    # loading the configurations of the neural network
    model = encoderTokens(100,10)
    # Loading in the train-set

    train_set = dump_load('base/trainLoader')

    # dataloader for the train-set
    train_loader = DataLoader(train_set,
                           batch_size=int(1),
                           shuffle=True)


    # dataloader for the test-set
    #number of epochs
    epochs = 1
    # This is the bestAcc we have till now
    bestAcc = 0
    for t in range(epochs):
        print(f"Epoch {t+1}\n-------------------------------")
        # Train loop
        train_loop(train_loader, model)